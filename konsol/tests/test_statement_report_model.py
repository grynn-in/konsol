"""Consolidated Statement Desk report, pure: konsol/close/statement_report_model.py.

The Desk Script Report "Consolidated Statement" shows the Close app's
Numbers statement (``statement_api.get_statement``'s payload) as report
rows. This module maps the payload to rows; it never recomputes an amount.

The statement under test is built by ``statement_model.statement`` itself,
from the fixtures in ``test_close_statement_model.py`` (loaded by path), so
the test exercises the real payload shape, not a hand-written copy of it.

Loaded by path; the module imports nothing from frappe or konsol.
"""
import ast
import importlib.util
import os

import pytest

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load(name, *parts):
    spec = importlib.util.spec_from_file_location(name, os.path.join(APP_DIR, *parts))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


R = _load("statement_report_model_under_test", "close", "statement_report_model.py")
F = _load("close_statement_model_fixtures", "tests", "test_close_statement_model.py")
SM = F.M


def _payload(rows=None, declared=None, **overrides):
    stmt = SM.statement(
        rows if rows is not None else F._s_rows(), F._s_accounts(), F._S_CALENDAR,
        F._S_KEY, declared if declared is not None else F._s_declared(),minor_unit=2
    )
    payload = {
        "state": "ok", "message": None, "statement": stmt,
        "period": {"fiscal_year": 2025, "fiscal_period": 7, "code": "P07",
                   "status": "Open", "period_type": "Regular"},
        "consolidation_group": "ZZ_GROUP", "reporting_currency": "USD",
        "gap": None, "not_included": None, "commentary": {}, "can_comment": False,
    }
    payload.update(overrides)
    return payload


def _by_label(data, label):
    found = [row for row in data if row["label"] == label]
    assert len(found) == 1, f"{label!r}: {len(found)} rows"
    return found[0]


def test_module_imports_nothing_from_frappe_or_konsol():
    with open(os.path.join(APP_DIR, "close", "statement_report_model.py")) as f:
        tree = ast.parse(f.read())
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            names = [node.module or ""]
        else:
            continue
        for name in names:
            assert not name.startswith(("frappe", "konsol")), name


def test_columns_are_label_code_four_amounts_and_currency():
    # Without a budget payload there is no budget column (BUD-4-2; review D1).
    fieldnames = [c["fieldname"] for c in R.columns()]
    assert fieldnames == ["label", "code", "current", "comparison", "variance", "ytd",
                          "prior_year", "prior_year_change", "currency", "commentary"]
    for column in R.columns():
        if column["fieldname"] in ("current", "comparison", "variance", "ytd"):
            assert column["fieldtype"] == "Currency"
            assert column["options"] == "currency"


def test_a_section_header_row_per_section_in_statement_order():
    data = R.rows(_payload())
    headers = [row["label"] for row in data if row.get("is_section")]
    assert headers == ["Profit and Loss", "Balance Sheet"]
    assert data[0]["label"] == "Profit and Loss"


def test_every_statement_line_has_a_row_with_the_payloads_own_amounts():
    payload = _payload()
    data = R.rows(payload)
    pl, bs = payload["statement"]["sections"]
    for line in pl["lines"] + bs["lines"]:
        label = line.get("heading_name") or line["label"]
        row = _by_label(data, label)
        assert row["current"] == line["current"]
        assert row["comparison"] == line.get("comparison")
        if line["kind"] != "residual":
            assert row["variance"] == line.get("variance")


def test_heading_rows_carry_their_code():
    data = R.rows(_payload())
    assert _by_label(data, "ASSETS")["code"] == "1"
    assert _by_label(data, "EQUITY")["code"] == "3"


def test_pl_rows_carry_ytd_and_bs_rows_do_not():
    payload = _payload()
    data = R.rows(payload)
    net = _by_label(data, SM.NET_RESULT_LABEL)
    net_line = payload["statement"]["sections"][0]["lines"][-1]
    assert net["ytd"] == net_line["ytd"]
    assert net["current"] == 241.43  # profit positive (fixture: P&L -241.43 net debit)
    for label in ("ASSETS", "LIABILITIES", "EQUITY", "Unmatched residual"):
        assert _by_label(data, label)["ytd"] is None


def test_balanced_fixture_bs_rows_and_zero_residual():
    data = R.rows(_payload())
    assert _by_label(data, "ASSETS")["current"] == 1735.10
    assert _by_label(data, "LIABILITIES")["current"] == 803.70
    assert _by_label(data, "Unmatched residual")["current"] == 0.0


def test_equity_includes_are_indented_rows_right_after_equity():
    data = R.rows(_payload())
    labels = [row["label"] for row in data]
    at = labels.index("EQUITY")
    includes = data[at + 1: at + 3]
    assert [row["label"] for row in includes] == [
        "of which currency translation (CTA)", "of which current-year result",
    ]
    for row in includes:
        assert row["indent"] == 1
    assert data[at]["indent"] == 0
    assert includes[0]["current"] == 5.07
    assert includes[1]["current"] == 241.43


def test_every_row_carries_the_reporting_currency():
    data = R.rows(_payload())
    assert data and all(row["currency"] == "USD" for row in data)


def test_unbalanced_residual_lists_its_explanation_indented():
    """A non-zero residual (CTA account undeclared): its explanation rows
    follow it, indented, with the Unexplained remainder — never hidden."""
    declared = F._s_declared(cta_account=None)
    data = R.rows(_payload(declared=declared))
    labels = [row["label"] for row in data]
    at = labels.index("Unmatched residual")
    assert data[at]["current"] != 0
    tail = data[at + 1:]
    assert tail and all(row["indent"] == 1 for row in tail)
    assert tail[-1]["label"] == "Unexplained"


@pytest.mark.parametrize("state, message", [
    ("choose_group", "Choose a consolidation group."),
    ("no_chart", "Publish the group chart (Main Account) first."),
    ("not_built", "The warehouse table is not built yet."),
    ("setup_gap", "statement_heading_side_undeclared: 2 have no normal_balance declared"),
])
def test_a_non_ok_state_raises_its_own_message_never_an_empty_table(state, message):
    payload = {"state": state, "message": message, "statement": None}
    with pytest.raises(ValueError) as exc:
        R.rows(payload)
    assert str(exc.value) == message


def test_a_non_ok_state_with_no_message_still_raises_naming_the_state():
    with pytest.raises(ValueError, match="error"):
        R.rows({"state": "error", "message": None, "statement": None})


def test_an_unknown_line_kind_raises_naming_it():
    payload = _payload()
    payload["statement"]["sections"][1]["lines"].append({"kind": "mystery", "label": "?"})
    with pytest.raises(ValueError, match="mystery"):
        R.rows(payload)


def test_message_names_period_group_currency_and_legend():
    text = R.message(_payload())
    assert "FY2025 P07" in text
    assert "ZZ_GROUP" in text
    assert "USD" in text
    assert SM.LEGEND in text


def test_message_carries_the_comparison_note_when_there_is_one():
    payload = _payload()
    payload["statement"]["periods"]["comparison_note"] = "No rows in the warehouse for FY2025 P06"
    assert "No rows in the warehouse for FY2025 P06" in R.message(payload)


def test_message_names_the_first_entities_not_included_and_counts_the_rest():
    codes = ["ZZ%02d" % i for i in range(R.NOT_INCLUDED_SHOWN + 5)]
    payload = _payload(not_included={"count": len(codes), "entities": codes, "hidden": 0})
    text = R.message(payload)
    assert "%d entities" % len(codes) in text
    assert codes[R.NOT_INCLUDED_SHOWN - 1] in text
    assert codes[R.NOT_INCLUDED_SHOWN] not in text
    assert "and 5 more" in text


def test_a_negative_zero_amount_is_shown_as_zero():
    """A residual rounded from a tiny negative is -0.0; it shows as 0.00."""
    payload = _payload()
    residual = payload["statement"]["sections"][1]["lines"][-1]
    residual["current"], residual["comparison"] = -0.0, -0.0
    row = _by_label(R.rows(payload), "Unmatched residual")
    assert str(row["current"]) == "0.0" and str(row["comparison"]) == "0.0"


def test_message_lines_are_html_escaped_and_joined_by_br():
    """Desk renders the report message as HTML."""
    text = R.message(_payload(consolidation_group="ZZ<b>G</b>"))
    assert "<b>" not in text and "ZZ&lt;b&gt;G&lt;/b&gt;" in text
    assert "<br>" in text


def test_message_carries_the_declared_accounts_gap_message():
    payload = _payload(gap={"code": "ZZ_GAP", "message": "Declare the CTA account."})
    assert "Declare the CTA account." in R.message(payload)


def test_message_names_entities_not_included():
    payload = _payload(not_included={"count": 2, "entities": ["ZZA", "ZZB"], "hidden": 0})
    text = R.message(payload)
    assert "ZZA" in text and "ZZB" in text


# --- Prior year (demo, 6 Nov 2026) -----------------------------------------
#
# ``rows(payload, prior)`` puts the prior year's value for the SAME line on
# every statement row: P&L lines the prior year's year to date, Balance
# Sheet lines the prior year's balance at the same period (its "current").
# Matched by heading code, by include kind, by kind for the other lines.
# A line absent last year shows a blank prior year, never 0.

def _prior(declared=None, **overrides):
    """The prior-year payload: the same fixture with every amount doubled,
    so each prior value differs from this year's."""
    doubled = [dict(r, amount=r["amount"] * 2) for r in F._s_rows()]
    overrides.setdefault("period", {"fiscal_year": 2024, "fiscal_period": 7, "code": "P07",
                                    "status": "Closed", "period_type": "Regular"})
    return _payload(rows=doubled, declared=declared, **overrides)


def _line(payload, section, match):
    lines = payload["statement"]["sections"][section]["lines"]
    found = [line for line in lines if all(line.get(k) == v for k, v in match.items())]
    assert len(found) == 1, match
    return found[0]


def test_columns_add_prior_year_and_change_after_ytd():
    fieldnames = [c["fieldname"] for c in R.columns()]
    assert fieldnames[fieldnames.index("ytd") + 1:] == [
        "prior_year", "prior_year_change", "currency", "commentary"]
    by_name = {c["fieldname"]: c for c in R.columns()}
    assert by_name["prior_year"]["label"] == "Prior year"
    assert by_name["prior_year_change"]["label"] == "Change vs prior year"
    for name in ("prior_year", "prior_year_change"):
        assert by_name[name]["fieldtype"] == "Currency"
        assert by_name[name]["options"] == "currency"


def test_pl_heading_takes_the_prior_years_ytd_matched_by_code():
    payload, prior = _payload(), _prior()
    row = _by_label(R.rows(payload, prior), "COST OF SALES")
    this_line = _line(payload, 0, {"heading": "4"})
    prior_line = _line(prior, 0, {"heading": "4"})
    assert prior_line["ytd"] != this_line["ytd"]
    assert row["prior_year"] == prior_line["ytd"]
    assert row["prior_year_change"] == round(this_line["ytd"] - prior_line["ytd"], 2)


def test_bs_heading_takes_the_prior_years_balance_matched_by_code():
    payload, prior = _payload(), _prior()
    data = R.rows(payload, prior)
    for code, label in (("1", "ASSETS"), ("2", "LIABILITIES"), ("3", "EQUITY")):
        prior_line = _line(prior, 1, {"heading": code})
        row = _by_label(data, label)
        assert row["prior_year"] == prior_line["current"]
        assert row["prior_year_change"] == round(row["current"] - prior_line["current"], 2)
    assert _by_label(data, "ASSETS")["prior_year"] == 3470.20


def test_equity_includes_take_the_prior_years_include_of_the_same_kind():
    payload, prior = _payload(), _prior()
    data = R.rows(payload, prior)
    includes = {i["kind"]: i for i in _line(prior, 1, {"heading": "3"})["includes"]}
    cta = _by_label(data, "of which currency translation (CTA)")
    result = _by_label(data, "of which current-year result")
    assert cta["prior_year"] == includes["cta"]["current"] == 10.14
    assert result["prior_year"] == includes["current_year_result"]["current"] == 482.86
    assert cta["prior_year_change"] == round(5.07 - 10.14, 2)


def test_net_result_takes_the_prior_years_net_result_ytd():
    payload, prior = _payload(), _prior()
    row = _by_label(R.rows(payload, prior), SM.NET_RESULT_LABEL)
    prior_line = _line(prior, 0, {"kind": "net_result"})
    assert row["prior_year"] == prior_line["ytd"] == 482.86
    assert row["prior_year_change"] == round(241.43 - 482.86, 2)


def test_residual_takes_the_prior_years_residual():
    declared = F._s_declared(cta_account=None)
    payload, prior = _payload(declared=declared), _prior(declared=declared)
    data = R.rows(payload, prior)
    prior_residual = _line(prior, 1, {"kind": "residual"})["current"]
    row = _by_label(data, "Unmatched residual")
    assert prior_residual and prior_residual != row["current"]
    assert row["prior_year"] == prior_residual
    assert row["prior_year_change"] == round(row["current"] - prior_residual, 2)
    # The residual's explanation rows are this year's breakdown only.
    at = [r["label"] for r in data].index("Unmatched residual")
    assert all(r["prior_year"] is None for r in data[at + 1:])


def test_a_heading_absent_last_year_has_a_blank_prior_year_not_zero():
    payload, prior = _payload(), _prior()
    bs_lines = prior["statement"]["sections"][1]["lines"]
    bs_lines[:] = [line for line in bs_lines if line.get("heading") != "2"]
    row = _by_label(R.rows(payload, prior), "LIABILITIES")
    assert row["prior_year"] is None
    assert row["prior_year_change"] is None


def test_section_header_rows_carry_no_prior_year():
    data = R.rows(_payload(), _prior())
    for row in data:
        if row.get("is_section"):
            assert row["prior_year"] is None and row["prior_year_change"] is None


def test_without_a_prior_payload_the_prior_columns_are_blank():
    data = R.rows(_payload())
    assert all(row["prior_year"] is None and row["prior_year_change"] is None for row in data)


def test_a_non_ok_prior_year_renders_blank_and_the_message_says_why():
    prior = {"state": "not_built", "message": "No rows in the warehouse for FY2024.",
             "statement": None}
    payload = _payload()
    data = R.rows(payload, prior)
    assert data == R.rows(payload)
    assert all(row["prior_year"] is None for row in data)
    text = R.message(payload, prior)
    assert "Prior year" in text and "FY2024 P07" in text
    assert "No rows in the warehouse for FY2024." in text


def test_a_non_ok_prior_year_with_no_message_names_its_state():
    text = R.message(_payload(), {"state": "error", "message": None, "statement": None})
    assert "Prior year" in text and "error" in text


def _tb(fiscal_year, fiscal_period):
    return {"fiscal_year": fiscal_year, "fiscal_period": fiscal_period, "main_account": "4100",
            "adjustment_type": "entity", "amount": 1.0, "null_rows": 0}


def test_prior_with_rows_keeps_a_prior_year_that_has_rows_up_to_its_period():
    prior = _prior()
    assert R.prior_with_rows(prior, [_tb(2024, 3)]) is prior


def test_prior_with_rows_refuses_an_ok_prior_year_with_no_rows():
    """FY2022 P12 live: state ok, no warehouse rows, every line 0.00."""
    prior = _prior()
    refused = R.prior_with_rows(prior, [])
    assert refused["state"] == "no_rows" and refused["statement"] is None
    assert refused["message"] == "No rows in the warehouse for FY2024 up to P07."
    assert all(row["prior_year"] is None for row in R.rows(_payload(), refused))


def test_prior_with_rows_ignores_rows_of_an_earlier_year_or_a_later_period():
    prior = _prior()
    assert R.prior_with_rows(prior, [_tb(2023, 12), _tb(2024, 8)])["state"] == "no_rows"


def test_prior_with_rows_passes_a_non_ok_prior_through():
    prior = {"state": "not_built", "message": "x", "statement": None}
    assert R.prior_with_rows(prior, None) is prior


def test_an_ok_prior_year_is_named_in_the_message_with_its_basis():
    text = R.message(_payload(), _prior())
    assert "Prior year: FY2024 P07" in text


# --- Commentary and drill (Desk, 6 Nov 2026) -------------------------------
#
# Each heading row carries the payload's own commentary for that heading
# (``get_statement``'s ``commentary``: ``{heading: {text, modified, ...}}``)
# and the payload's own ``can_comment`` (the rule is statement_api's; the
# report never re-decides it). The Commentary column shows the text,
# truncated; ``commentary_text`` and ``commentary_modified`` are what the
# Add commentary dialog prefills and sends back as the stale-edit token.
# ``drill_group`` (the Link filter value) and ``consolidation_group`` (the
# group code commentary is keyed on) let a heading open Statement Drill.

def _commented(**overrides):
    commentary = {"1": {"name": "SC-ZZ_GROUP-2025-7-1", "text": "Cash up on the bond issue.",
                        "by": "ZZ User", "at": "2025-08-01T10:00:00+00:00",
                        "modified": "2025-08-01 10:00:00.000001"}}
    return _payload(commentary=commentary, **overrides)


def test_commentary_column_is_data():
    by_name = {c["fieldname"]: c for c in R.columns()}
    assert by_name["commentary"]["fieldtype"] == "Data"
    assert by_name["commentary"]["label"] == "Commentary"


def test_a_heading_row_shows_its_commentary_and_carries_text_and_token():
    row = _by_label(R.rows(_commented()), "ASSETS")
    assert row["commentary"] == "Cash up on the bond issue."
    assert row["commentary_text"] == "Cash up on the bond issue."
    assert row["commentary_modified"] == "2025-08-01 10:00:00.000001"


def test_a_heading_without_commentary_is_blank_with_no_token():
    row = _by_label(R.rows(_commented()), "LIABILITIES")
    assert row["commentary"] is None
    assert row["commentary_text"] is None
    assert row["commentary_modified"] is None


def test_non_heading_rows_carry_no_commentary_and_cannot_be_commented():
    for row in R.rows(_commented(can_comment=True)):
        if not row.get("code"):
            assert row["commentary"] is None
            assert not row.get("can_comment")


def test_long_commentary_is_truncated_with_an_ellipsis_full_text_kept():
    text = "x" * (R.COMMENTARY_SHOWN + 20)
    row = _by_label(R.rows(_payload(commentary={"3": {"text": text, "modified": "m"}})), "EQUITY")
    assert len(row["commentary"]) == R.COMMENTARY_SHOWN
    assert row["commentary"].endswith("…")
    assert row["commentary_text"] == text


def test_a_blank_commentary_text_is_blank_but_keeps_its_token():
    """A cleared commentary still exists: the next save must send its token."""
    row = _by_label(R.rows(_payload(commentary={"3": {"text": "", "modified": "m1"}})), "EQUITY")
    assert row["commentary"] is None
    assert row["commentary_modified"] == "m1"


@pytest.mark.parametrize("value, expected", [(True, 1), (False, 0)])
def test_heading_rows_carry_the_payloads_own_can_comment(value, expected):
    headings = [row for row in R.rows(_payload(can_comment=value)) if row.get("code")]
    assert headings and all(row["can_comment"] == expected for row in headings)


@pytest.mark.parametrize("key", ["can_comment", "commentary"])
def test_an_ok_payload_without_the_commentary_keys_is_refused_naming_the_key(key):
    payload = _payload()
    del payload[key]
    with pytest.raises(ValueError, match=key):
        R.rows(payload)


def test_heading_rows_carry_the_drill_group_and_the_group_code():
    headings = [row for row in R.rows(_payload(), drill_group="CG-ZZ_GROUP-") if row.get("code")]
    assert headings
    for row in headings:
        assert row["drill_group"] == "CG-ZZ_GROUP-"
        assert row["consolidation_group"] == "ZZ_GROUP"


# --- Basis: Entity (konsol#495 row 2) ---------------------------------------

def _entity_payload(**overrides):
    base = dict(basis="Entity", entity="ZZA", entity_name="Zz Entity",
                consolidation_group=None, reporting_currency="EUR")
    base.update(overrides)
    return _payload(**base)


def test_entity_message_header_names_basis_entity_name_and_currency():
    first = R.message(_entity_payload()).split("<br>")[0]
    assert first == "Numbers · FY2025 P07 · Basis: Entity · ZZA Zz Entity · EUR"


def test_group_message_header_is_unchanged_without_basis():
    first = R.message(_payload()).split("<br>")[0]
    assert first == "Numbers · FY2025 P07 · ZZ_GROUP · USD"


def test_entity_heading_rows_carry_drill_entity():
    headings = [row for row in R.rows(_entity_payload()) if row.get("code")]
    assert headings
    for row in headings:
        assert row["drill_entity"] == "ZZA"


def test_group_heading_rows_have_no_drill_entity():
    headings = [row for row in R.rows(_payload()) if row.get("code")]
    assert headings
    for row in headings:
        assert row["drill_entity"] is None


def test_has_rows_through_is_one_year_and_up_to_the_period():
    """#495 V2: the one no_rows rule, shared with the entity statement."""
    assert R.has_rows_through([_tb(2024, 3)], 2024, 7) is True
    assert R.has_rows_through([_tb(2024, 7)], 2024, 7) is True
    assert R.has_rows_through([_tb(2023, 12)], 2024, 7) is False
    assert R.has_rows_through([_tb(2024, 8)], 2024, 7) is False
    assert R.has_rows_through([], 2024, 7) is False
    assert R.has_rows_through(None, 2024, 7) is False


# --- Budget columns (konsol#348 S5 K6; S5-Q4 a, S5-Q6 a) --------------------
#
# ``rows(payload, prior, drill_group, budget)`` merges a budget payload per
# line, the Prior year pattern: the budget statement is statement_model's own
# statement over gold_fully_consolidated_budget rows (same chart, periods,
# minor unit and display sign), so Budget is the budget line's ``current``,
# Variance to budget is ``current - budget``, Budget year to date the budget
# line's ``ytd`` (P&L lines only). A Balance Sheet budget value is the
# budgeted movement year to date (a budget has Regular-period rows only), and
# the message says so (S5-Q4 a). Blank, never 0, where the budget is absent or
# not ok; the message carries the state's sentence then.

UNBALANCED_LABEL = "Unbalanced budget (no balancing entry)"
_BUDGET_META = {"cycle": "BCYC-ZZB-2025-abc12345", "layers": ["base", "board"], "rate_set": "ZZRS",
                "scenario_id": "ZZB", "declaration": "APB-CG-ZZ_GROUP--2025-0f0f0f0f",
                # S6 KB2: the chart's normal balance per heading; the P&L heading
                # 4 declares none in the live-shaped chart (S3: 6 of 88 blank).
                "heading_sides": {"1": "Debit", "2": "Credit", "3": "Credit", "4": ""}}
_BUDGET_OK_SENTENCE = ("Approved budget for ZZ_GROUP FY2025: Budget Cycle BCYC-ZZB-2025-abc12345, all layers "
                       "(base, board), Rate Set ZZRS (Approved Budget APB-CG-ZZ_GROUP--2025-0f0f0f0f, approved by "
                       "lead@example.com on 2026-11-17).")


def _budget_rows():
    """FCB shape: a revenue budget of 1,000 and a cash budget of 300 with no
    balancing entry, so the plug is +700 for the (entity, period)."""
    return [
        F._row("4100", -1000.0, adjustment_type="entity"),
        F._row("1110", 300.0, adjustment_type="entity"),
        F._row("UNBALANCED", 700.0, adjustment_type="unbalanced"),
    ]


def _budget(rows=None, state="ok", message=_BUDGET_OK_SENTENCE, **meta):
    stmt = SM.statement(rows if rows is not None else _budget_rows(), F._s_accounts(), F._S_CALENDAR,
                        F._S_KEY, F._s_declared(), minor_unit=2) if state == "ok" else None
    return {"state": state, "message": message, "statement": stmt, "meta": {**_BUDGET_META, **meta}}


def test_columns_carry_the_budget_columns_only_for_an_ok_budget():
    """BUD-4-2 (Deepak Pai, 7 Nov 2026): "Numbers shows a visible 'no approved
    budget' state, never a blank column". The three budget columns follow
    Year to date when, and only when, the budget payload is ok; without a
    budget, or with a non-ok one, the report has no budget column at all and
    the message carries the state (PR #554 review D1)."""
    actuals = [c["fieldname"] for c in R.columns()]
    assert "budget" not in actuals and "variance_to_budget" not in actuals and "budget_ytd" not in actuals
    assert actuals[actuals.index("ytd") + 1] == "prior_year"
    for state in ("no_approved_budget", "cycle_cancelled", "inputs_changed", "layer_missing", "error", "no_rows"):
        assert [c["fieldname"] for c in R.columns(2, budget=_budget(state=state, message="x"))] == actuals, state
    fieldnames = [c["fieldname"] for c in R.columns(budget=_budget())]
    assert fieldnames[fieldnames.index("ytd") + 1:fieldnames.index("prior_year")] == [
        "budget", "variance_to_budget", "favourability", "budget_ytd"]
    by_name = {c["fieldname"]: c for c in R.columns(minor_unit=2, budget=_budget())}
    assert by_name["budget"]["label"] == "Budget (before eliminations)"
    assert by_name["variance_to_budget"]["label"] == "Variance to budget"
    assert by_name["budget_ytd"]["label"] == "Budget year to date"
    for name in ("budget", "variance_to_budget", "budget_ytd"):
        assert by_name[name]["fieldtype"] == "Currency" and by_name[name]["options"] == "currency"
        assert by_name[name]["precision"] == 2


def test_without_a_budget_the_budget_columns_are_blank():
    data = R.rows(_payload())
    assert data and all(row["budget"] is None and row["variance_to_budget"] is None
                        and row["budget_ytd"] is None for row in data)


def test_a_pl_heading_takes_the_budgets_current_variance_and_ytd_matched_by_code():
    payload, budget = _payload(), _budget()
    row = _by_label(R.rows(payload, budget=budget), "COST OF SALES")
    this_line = _line(payload, 0, {"heading": "4"})
    budget_line = _line(budget, 0, {"heading": "4"})
    assert budget_line["current"] == 1000.0 and this_line["current"] == 241.43
    assert row["budget"] == 1000.0
    assert row["variance_to_budget"] == round(241.43 - 1000.0, 2)
    assert row["budget_ytd"] == budget_line["ytd"] == 1000.0


def test_net_result_takes_the_budgets_net_result():
    row = _by_label(R.rows(_payload(), budget=_budget()), SM.NET_RESULT_LABEL)
    assert row["budget"] == 1000.0 and row["budget_ytd"] == 1000.0
    assert row["variance_to_budget"] == round(241.43 - 1000.0, 2)


def test_a_bs_heading_takes_the_budgeted_movement_year_to_date_with_no_budget_ytd():
    """S5-Q4 (a): the budget's cumulative BS value IS the year's budgeted
    movements (Regular-period rows only); the Budget year to date column
    stays P&L-only, as on the actuals."""
    data = R.rows(_payload(), budget=_budget())
    assets = _by_label(data, "ASSETS")
    assert assets["budget"] == 300.0
    assert assets["budget_ytd"] is None
    # A budget line present with nothing budgeted is 0.00, not blank.
    liabilities = _by_label(data, "LIABILITIES")
    assert liabilities["budget"] == 0.0 and str(liabilities["budget"]) == "0.0"


def test_a_bs_line_has_no_variance_to_budget():
    """S5 F1 (coordinator, 17 Nov 2026): under S5-Q4 (a) the BS budget is a
    movement year to date and the actual is a balance; their difference is
    not a variance, so the cell is blank on every Balance Sheet line (a
    heading, an include, the plug) while the Budget value stays. The P&L
    keeps its variance, and the legend says why the BS has none."""
    data = R.rows(_payload(), budget=_budget())
    for label in ("ASSETS", "LIABILITIES", "EQUITY", "of which current-year result",
                  "of which currency translation (CTA)"):
        row = _by_label(data, label)
        assert row["budget"] is not None, label
        assert row["variance_to_budget"] is None, label
    assert _by_label(data, "COST OF SALES")["variance_to_budget"] == round(241.43 - 1000.0, 2)
    text = R.message(_payload(), budget=_budget())
    assert "no variance to budget on the balance sheet" in text.lower()


def test_equity_includes_take_the_budgets_include_of_the_same_kind():
    data = R.rows(_payload(), budget=_budget())
    result = _by_label(data, "of which current-year result")
    assert result["budget"] == 1000.0
    assert result["variance_to_budget"] is None   # a BS line (S5 F1)
    cta = _by_label(data, "of which currency translation (CTA)")
    assert cta["budget"] == 0.0   # the budget has a CTA bucket of 0: present, zero


def test_a_budget_only_unbalanced_line_is_laid_out_before_the_residual():
    """Actuals never carry the plug; the budget's shows as its own row with
    blank actuals columns, so the budget balance sheet is seen to foot."""
    data = R.rows(_payload(), budget=_budget())
    labels = [row["label"] for row in data]
    at = labels.index(UNBALANCED_LABEL)
    assert labels[at + 1] == "Unmatched residual"
    row = data[at]
    assert row["current"] is None and row["comparison"] is None and row["variance"] is None
    assert row["budget"] == 700.0
    assert row["variance_to_budget"] is None, "no actual to compare with"
    assert row["budget_ytd"] is None and row["prior_year"] is None
    residual = _by_label(data, "Unmatched residual")
    assert residual["budget"] == 0.0


def test_the_budget_only_unbalanced_row_carries_the_plugs_code_and_the_drill_group():
    """S5 K10b: the plug's own drill (get_budget_drill, heading
    "UNBALANCED") opens from the row's Budget cell, so the row carries the
    warehouse's literal main_account as its code and the group the drill
    takes; the label is not a heading link (no chart heading behind it)."""
    data = R.rows(_payload(), drill_group="CG-ZZG-", budget=_budget())
    row = _by_label(data, UNBALANCED_LABEL)
    assert row["code"] == "UNBALANCED"
    assert row["drill_group"] == "CG-ZZG-"
    assert R.UNBALANCED_CODE == "UNBALANCED"
    # Heading rows are unchanged: code is the chart heading, drill_group the group.
    assert _by_label(data, "LIABILITIES")["code"] == "2"


def test_an_ok_budget_marks_the_heading_and_unbalanced_rows_for_the_budget_drill():
    """S5 K10b: ``drill_budget`` on the rows whose Budget cell opens the
    budget drill (Statement Drill, basis Budget): heading rows with a budget
    value and the plug's row. Includes, net result, section headers and the
    residual carry 0. Without a group to drill on, nothing is marked."""
    data = R.rows(_payload(), drill_group="CG-ZZG-", budget=_budget())
    flagged = [row["label"] for row in data if row.get("drill_budget")]
    assert UNBALANCED_LABEL in flagged
    assert all(row["code"] for row in data if row.get("drill_budget"))
    headings = [row["label"] for row in data if row.get("code") and row["code"] != "UNBALANCED"]
    assert [h for h in headings if h in flagged] == headings, "every heading with a budget value"
    for label in ("Profit and Loss", "Net result", "Unmatched residual"):
        assert not _by_label(data, label).get("drill_budget")
    assert not any(row.get("drill_budget") for row in R.rows(_payload(), drill_group=None, budget=_budget()))


def test_a_non_ok_or_absent_budget_marks_no_row_for_the_budget_drill():
    for budget in (None, {"state": "no_approved_budget", "message": "No approved budget.",
                          "statement": None, "meta": {}}):
        data = R.rows(_payload(), drill_group="CG-ZZG-", budget=budget)
        assert not any(row.get("drill_budget") for row in data)
        assert all("drill_budget" in row for row in data if row.get("code")), "the key is always present"


def test_an_actuals_unbalanced_line_is_laid_out_and_takes_the_budgets_by_kind():
    data = R.rows(_payload(rows=_budget_rows()), budget=_budget())
    row = _by_label(data, UNBALANCED_LABEL)
    assert row["current"] == 700.0 and row["budget"] == 700.0
    assert row["variance_to_budget"] is None   # a BS line (S5 F1)
    # Without a budget the line still lays out (no unknown-kind refusal).
    assert _by_label(R.rows(_payload(rows=_budget_rows())), UNBALANCED_LABEL)["current"] == 700.0


# --- Unbudgeted is budget zero (Deepak Pai, 17 Nov 2026, konsol#348 comment
# 6956694095; the 22 Sep 2026 rule, gold_variance_analysis's
# coalesce(budget_amount, 0)) -----------------------------------------------
#
# statement_model.statement emits every chart heading, at 0 when no row sits
# under it, and always the net result, so an ok budget carries every heading
# the actuals do: an unbudgeted heading shows Budget 0.00 with no special case
# here (test_close_statement_api's S5-path test pins it end to end, drill
# included; PR #558 review m1). A payload that lacks a line is not one the
# reader builds: it is blank like any other absent line, never 0.00.

def test_a_heading_absent_from_the_budget_payload_is_blank_not_zero():
    budget = _budget()
    bs_lines = budget["statement"]["sections"][1]["lines"]
    bs_lines[:] = [line for line in bs_lines if line.get("heading") != "2"]
    row = _by_label(R.rows(_payload(), budget=budget), "LIABILITIES")
    assert row["budget"] is None and row["variance_to_budget"] is None
    assert row["favourability"] is None and row["drill_budget"] == 0


def test_the_report_model_has_no_unbudgeted_special_case():
    """PR #558 review m1: the branch that set an absent heading to 0.00 was
    never taken on a payload statement_model builds."""
    assert not hasattr(R, "_has_budget_line")


def test_without_an_ok_budget_an_unbudgeted_heading_stays_blank():
    for budget in (None, _budget(state="no_approved_budget", message="No approved budget.")):
        row = _by_label(R.rows(_payload(), budget=budget), "COST OF SALES")
        assert row["budget"] is None and row["variance_to_budget"] is None
        assert row["favourability"] is None and row["budget_ytd"] is None


def test_message_states_that_an_unbudgeted_line_is_budget_zero():
    assert R.UNBUDGETED_RULE in R.message(_payload(), budget=_budget())
    assert "zero" in R.UNBUDGETED_RULE and "#" not in R.UNBUDGETED_RULE


def test_section_headers_and_residual_explanations_carry_no_budget():
    declared = F._s_declared(cta_account=None)
    data = R.rows(_payload(declared=declared), budget=_budget())
    for row in data:
        if row.get("is_section"):
            assert row["budget"] is None and row["variance_to_budget"] is None
    at = [r["label"] for r in data].index("Unmatched residual")
    assert all(r["budget"] is None for r in data[at + 1:])


@pytest.mark.parametrize("state, sentence", [
    ("no_approved_budget", "No approved budget for ZZ_GROUP FY2025: declare one in Approved Budget."),
    ("cycle_cancelled", "Approved Budget APB for ZZ_GROUP FY2025 names Budget Cycle B, which is cancelled."),
    ("inputs_changed", "Approved Budget APB for ZZ_GROUP FY2025: the inputs of Budget Cycle B have changed."),
    ("layer_missing", "Approved Budget APB for ZZ_GROUP FY2025 reads layer gone, which is no Budget Layer."),
])
def test_a_non_ok_budget_renders_no_budget_columns_and_the_message_carries_its_sentence(state, sentence):
    """BUD-4-2: a visible state, never a blank column (PR #554 review D1)."""
    payload = _payload()
    budget = _budget(state=state, message=sentence)
    data = R.rows(payload, budget=budget)
    assert data == R.rows(payload)
    assert all(row["budget"] is None for row in data)
    assert R.columns(2, budget=budget) == R.columns(2), "no budget column to leave blank"
    text = R.message(payload, budget=budget)
    assert sentence in text
    assert "Budget columns not shown: " + sentence in text, text
    assert "blank" not in text.lower(), "there is no blank column to explain"


def test_a_non_ok_budget_with_no_sentence_still_names_its_state():
    text = R.message(_payload(), budget={"state": "error", "message": None, "statement": None, "meta": {}})
    assert "Budget" in text and "error" in text


def test_message_names_cycle_layers_rate_set_and_the_limits_for_an_ok_budget():
    text = R.message(_payload(), budget=_budget())
    for word in ("BCYC-ZZB-2025-abc12345", "base, board", "ZZRS", "APB-CG-ZZ_GROUP--2025-0f0f0f0f"):
        assert word in text, word
    assert "before intercompany eliminations and without group-level adjustments" in text
    assert "budgeted movements year to date" in text   # S5-Q4 (a)
    assert "#" not in text, "no issue numbers in user text"


def test_message_has_no_budget_line_without_a_budget():
    assert "udget" not in R.message(_payload())


# --- budget-only lines the actuals lack (PR #554 review M1) -------------------
#
# A budget can carry a non-heading line the actuals do not: a P&L or BS "No
# heading" line (a leaf with no parent heading), a "Not in the group chart"
# line, or the plug. Each is laid out as its own row with blank actuals, where
# the actuals' line would stand, so the Budget column foots on screen (the
# P&L Net result equals its headings plus No heading). Only the plug was.

def _budget_with_lines_the_actuals_lack():
    """Budget rows: COST OF SALES -1,000; a P&L leaf 4900 with no heading
    -50; a code 9999 outside the chart 120 on the Balance Sheet; cash 300;
    the plug closes the entity's local imbalance (-(−1000 − 50 + 120 + 300)
    = 630). The actuals (``_payload()``) carry neither extra line."""
    accounts = F._s_accounts()
    accounts["4900"] = {"account_name": "Other income", "parent_account": None, "is_group": False,
                        "statement_section": SM.PL, "lft": 15}
    rows = [F._row("4100", -1000.0, adjustment_type="entity"),
            F._row("4900", -50.0, adjustment_type="entity"),
            F._row("9999", 120.0, adjustment_type="entity"),
            F._row("1110", 300.0, adjustment_type="entity"),
            F._row("UNBALANCED", 630.0, adjustment_type="unbalanced")]
    stmt = SM.statement(rows, accounts, F._S_CALENDAR, F._S_KEY, F._s_declared(), minor_unit=2)
    return {"state": "ok", "message": _BUDGET_OK_SENTENCE, "statement": stmt, "meta": dict(_BUDGET_META)}


def test_budget_only_no_heading_and_not_in_chart_lines_are_laid_out_and_the_net_result_foots():
    payload, budget = _payload(), _budget_with_lines_the_actuals_lack()
    without = [r["label"] for r in R.rows(payload)]
    assert "No heading" not in without and not any(l.startswith("Not in the group chart") for l in without)
    assert _line(budget, 0, {"kind": "no_heading"})["current"] == 50.0
    assert _line(budget, 1, {"kind": "not_in_chart"})["current"] == 120.0

    data = R.rows(payload, drill_group="CG-ZZG-", budget=budget)
    labels = [r["label"] for r in data]
    no_heading = _by_label(data, "No heading")
    assert labels.index("COST OF SALES") < labels.index("No heading") < labels.index(SM.NET_RESULT_LABEL)
    assert no_heading["current"] is None and no_heading["comparison"] is None and no_heading["ytd"] is None
    assert no_heading["budget"] == 50.0 and no_heading["budget_ytd"] == 50.0
    assert no_heading["variance_to_budget"] is None, "no actual to compare with"
    net = _by_label(data, SM.NET_RESULT_LABEL)
    assert net["budget"] == 1050.0 == _by_label(data, "COST OF SALES")["budget"] + no_heading["budget"]
    assert net["budget_ytd"] == 1050.0

    not_in_chart = _by_label(data, "Not in the group chart (9999)")
    assert labels.index("EQUITY") < labels.index("Not in the group chart (9999)") \
        < labels.index(UNBALANCED_LABEL) < labels.index("Unmatched residual")
    assert not_in_chart["current"] is None and not_in_chart["comparison"] is None
    assert not_in_chart["budget"] == 120.0 and not_in_chart["variance_to_budget"] is None
    assert not_in_chart["budget_ytd"] is None
    # Neither is a heading or the plug: no code, no drill, no commentary.
    for row in (no_heading, not_in_chart):
        assert row["code"] is None and not row.get("drill_budget") and row.get("drill_group") is None
        assert row["commentary"] is None and row["prior_year"] is None
    assert _by_label(data, UNBALANCED_LABEL)["budget"] == 630.0


def test_a_budget_only_line_is_not_added_when_the_actuals_carry_it():
    """Actuals with their own No heading / Not in the group chart lines take
    the budget's values by kind, and no second row appears."""
    accounts = F._s_accounts()
    accounts["4900"] = {"account_name": "Other income", "parent_account": None, "is_group": False,
                        "statement_section": SM.PL, "lft": 15}
    rows = F._s_rows() + [F._row("4900", -10.0), F._row("9999", 5.0)]
    stmt = SM.statement(rows, accounts, F._S_CALENDAR, F._S_KEY, F._s_declared(), minor_unit=2)
    payload = _payload(statement=stmt)
    data = R.rows(payload, budget=_budget_with_lines_the_actuals_lack())
    no_heading = _by_label(data, "No heading")
    assert no_heading["current"] == 10.0 and no_heading["budget"] == 50.0
    assert no_heading["variance_to_budget"] == -40.0
    not_in_chart = _by_label(data, "Not in the group chart (9999)")
    assert not_in_chart["current"] == 5.0 and not_in_chart["budget"] == 120.0


# --- Favourability (konsol#348 S6 KB3; S6-Q3 a, BUD-7-a) ------------------------
#
# A text attribute of a Profit and Loss heading row and the net result, never a
# number (konsolidat#214: a flag that sums is wrong on the report too). The
# budget payload's meta.heading_sides (KB2) carries the chart's normal balance
# per heading. Amounts are display-signed (income positive, costs in brackets),
# so for a declared side, Credit or Debit alike, a variance to budget above
# zero is Favourable and below zero Unfavourable; zero is On budget; a heading
# with no declared side reads n/a (BUD-7-a: reported, never refused, never
# guessed). The net result is favourable when profit is higher (a declared
# sub-rule, in the legend). Balance Sheet lines carry none (no variance there,
# S5 F1).

def _sides(**sides):
    return {**_BUDGET_META["heading_sides"], **sides}


def test_columns_add_favourability_as_text_after_variance_to_budget_only_for_an_ok_budget():
    fieldnames = [c["fieldname"] for c in R.columns(minor_unit=2, budget=_budget())]
    assert fieldnames[fieldnames.index("ytd") + 1:fieldnames.index("prior_year")] == [
        "budget", "variance_to_budget", "favourability", "budget_ytd"]
    column = next(c for c in R.columns(minor_unit=2, budget=_budget()) if c["fieldname"] == "favourability")
    assert column["label"] == "Favourability" and column["fieldtype"] == "Data"
    assert "precision" not in column and column.get("options") is None
    assert "favourability" not in [c["fieldname"] for c in R.columns(minor_unit=2)]
    assert "favourability" not in [c["fieldname"] for c in R.columns(minor_unit=2, budget=_budget(state="no_rows"))]


def test_a_declared_credit_heading_reads_by_the_sign_of_the_variance():
    # COST OF SALES (heading 4) holds revenue leaf 4100: actual 241.43 (display)
    # against a budget of 1,000: less income than budgeted -> Unfavourable.
    row = _by_label(R.rows(_payload(), budget=_budget(heading_sides=_sides(**{"4": "Credit"}))), "COST OF SALES")
    assert row["variance_to_budget"] < 0 and row["favourability"] == R.UNFAVOURABLE == "Unfavourable"
    # A budget of 200: more income than budgeted -> Favourable.
    small = [F._row("4100", -200.0, adjustment_type="entity"), F._row("1110", 200.0, adjustment_type="entity")]
    row = _by_label(R.rows(_payload(), budget=_budget(rows=small, heading_sides=_sides(**{"4": "Credit"}))),
                    "COST OF SALES")
    assert row["variance_to_budget"] > 0 and row["favourability"] == R.FAVOURABLE == "Favourable"


def test_a_declared_debit_heading_reads_the_same_way_under_display_signs():
    """Debit and Credit read alike: display signs already put income positive
    and costs negative, so "above budget is favourable" holds for both. A cost
    heading whose costs exceed the budget shows a negative variance."""
    accounts = F._s_accounts()
    accounts["4"]["normal_balance"] = "Debit"
    rows = F._s_rows()[:-1] + [F._row("4100", 300.0)]            # costs 300 (display -300)
    stmt = SM.statement(rows, accounts, F._S_CALENDAR, F._S_KEY, F._s_declared(), minor_unit=2)
    budget_rows = [F._row("4100", 250.0, adjustment_type="entity"), F._row("1110", -250.0, adjustment_type="entity")]
    budget = _budget(rows=budget_rows, heading_sides=_sides(**{"4": "Debit"}))
    row = _by_label(R.rows(_payload(statement=stmt), budget=budget), "COST OF SALES")
    assert row["budget"] == -250.0 and row["variance_to_budget"] == -50.0
    assert row["favourability"] == "Unfavourable", "more cost than budgeted"
    budget_rows = [F._row("4100", 350.0, adjustment_type="entity"), F._row("1110", -350.0, adjustment_type="entity")]
    row = _by_label(R.rows(_payload(statement=stmt), budget=_budget(rows=budget_rows, heading_sides=_sides(**{"4": "Debit"}))),
                    "COST OF SALES")
    assert row["variance_to_budget"] == 50.0 and row["favourability"] == "Favourable"


def test_a_zero_variance_on_a_declared_heading_is_on_budget():
    rows = [F._row("4100", -241.43, adjustment_type="entity"), F._row("1110", 241.43, adjustment_type="entity")]
    row = _by_label(R.rows(_payload(), budget=_budget(rows=rows, heading_sides=_sides(**{"4": "Credit"}))), "COST OF SALES")
    assert row["variance_to_budget"] == 0.0 and row["favourability"] == R.ON_BUDGET == "On budget"


def test_a_heading_with_no_declared_side_reads_n_a_whatever_the_variance():
    """BUD-7-a: an undeclared normal balance is reported, never refused and
    never given a direction; the live-shaped chart's heading 4 is blank."""
    row = _by_label(R.rows(_payload(), budget=_budget()), "COST OF SALES")
    assert row["variance_to_budget"] != 0 and row["favourability"] == R.FAVOURABILITY_NA == "n/a"
    rows = [F._row("4100", -241.43, adjustment_type="entity"), F._row("1110", 241.43, adjustment_type="entity")]
    row = _by_label(R.rows(_payload(), budget=_budget(rows=rows)), "COST OF SALES")
    assert row["variance_to_budget"] == 0.0 and row["favourability"] == "n/a"


def test_the_net_result_reads_more_profit_is_favourable():
    data = R.rows(_payload(), budget=_budget())
    net = _by_label(data, SM.NET_RESULT_LABEL)
    assert net["variance_to_budget"] < 0 and net["favourability"] == "Unfavourable"
    rows = [F._row("4100", -200.0, adjustment_type="entity"), F._row("1110", 200.0, adjustment_type="entity")]
    assert _by_label(R.rows(_payload(), budget=_budget(rows=rows)), SM.NET_RESULT_LABEL)["favourability"] == "Favourable"
    rows = [F._row("4100", -241.43, adjustment_type="entity"), F._row("1110", 241.43, adjustment_type="entity")]
    assert _by_label(R.rows(_payload(), budget=_budget(rows=rows)), SM.NET_RESULT_LABEL)["favourability"] == "On budget"


def test_balance_sheet_section_headers_and_budget_only_rows_carry_no_favourability():
    data = R.rows(_payload(), budget=_budget(heading_sides=_sides(**{"4": "Credit"})))
    for label in ("ASSETS", "LIABILITIES", "EQUITY", "of which current-year result",
                  "of which currency translation (CTA)", "Unmatched residual", UNBALANCED_LABEL,
                  "Profit and Loss", "Balance Sheet"):
        assert _by_label(data, label)["favourability"] is None, label
    assert all("favourability" in row for row in data), "the key is always present"


def test_without_a_budget_or_with_a_non_ok_one_favourability_is_blank_and_unmentioned():
    data = R.rows(_payload())
    assert all(row["favourability"] is None for row in data)
    assert "avourab" not in R.message(_payload())
    budget = _budget(state="no_approved_budget", message="No approved budget for ZZ_GROUP FY2025.")
    assert all(row["favourability"] is None for row in R.rows(_payload(), budget=budget))
    assert "avourab" not in R.message(_payload(), budget=budget)


def test_an_ok_budget_without_heading_sides_is_refused_naming_the_key():
    budget = _budget()
    del budget["meta"]["heading_sides"]
    with pytest.raises(ValueError, match="heading_sides"):
        R.rows(_payload(), budget=budget)


def test_message_states_the_favourability_rule_for_an_ok_budget():
    text = R.message(_payload(), budget=_budget())
    assert "Favourability" in text
    for phrase in ("normal balance", "n/a", "On budget", "net result", "more profit"):
        assert phrase in text, phrase
    assert "#" not in text

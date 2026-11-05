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

"""Statement Drill Desk report, pure: konsol/close/statement_drill_report_model.py.

The Desk Script Report "Statement Drill" shows ``statement_api.get_drill``'s
payload (``drill_model.drill``) as a report tree: one row per layer (each
entity, intercompany eliminations, CTA, top-side journals, deal rows, the
current-year result, rounding), its accounts under it, and the top-side
journals under their own row; each row links its source document where the
payload names one. Nothing here recomputes an amount.

The drill under test is built by ``drill_model.drill`` itself, from the
fixtures in ``test_close_drill_model.py`` (loaded by path), and its
statement line by ``statement_model.statement``: the real producers, not
hand-written copies of their output (R31).

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


D = _load("statement_drill_report_model_under_test", "close", "statement_drill_report_model.py")
R = _load("statement_report_model_for_drill_report", "close", "statement_report_model.py")
F = _load("close_drill_model_fixtures", "tests", "test_close_drill_model.py")
DM, SM = F.M, F.SM

_PERIOD = {"fiscal_year": F._KEY[0], "fiscal_period": F._KEY[1], "code": "P07",
           "status": "Open", "period_type": "Regular"}


def _keys(heading):
    return F._KEYS if F._accounts()[heading]["statement_section"] == SM.BS else {F._KEY}


def _payload(rows, heading, journals=()):
    """``get_drill``'s payload for ``heading`` over ``rows``."""
    line = F._statement_line(rows, heading)
    drill = DM.drill(rows, list(journals), F._accounts(), heading, _keys(heading),
                     F._declared(), None, line, minor_unit=2)
    return {"period": dict(_PERIOD), "consolidation_group": "ZZ_GROUP", "heading": heading,
            "state": "ok", "message": None, "drill": drill}


def _statement_payload(rows):
    """``get_statement``'s payload over the same rows (for the
    Consolidated Statement report's own heading row)."""
    stmt = SM.statement(rows, F._accounts(), F._CALENDAR, F._KEY, F._declared(), minor_unit=2)
    return {"state": "ok", "message": None, "statement": stmt, "period": dict(_PERIOD),
            "consolidation_group": "ZZ_GROUP", "reporting_currency": "USD", "gap": None,
            "not_included": None, "commentary": {}, "can_comment": False}


def _layer_rows(data):
    return [row for row in data if row["indent"] == 0 and not row.get("is_total")]


def _total(data):
    found = [row for row in data if row.get("is_total")]
    assert len(found) == 1
    return found[0]


def _pl_rows():
    return [
        F._row("4100", -300.0, "entity", "ZZA"),
        F._row("4100", -200.004, "entity", "ZZB"),
        F._row("4100", 50.0, "ic_elimination", ""),
    ]


def test_module_imports_nothing_from_frappe_or_konsol():
    with open(os.path.join(APP_DIR, "close", "statement_drill_report_model.py")) as f:
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


def test_columns():
    by_name = {c["fieldname"]: c for c in D.columns()}
    assert list(by_name) == ["label", "entity", "main_account", "amount", "source_doctype",
                             "source", "currency"]
    assert by_name["amount"]["fieldtype"] == "Currency"
    assert by_name["amount"]["options"] == "currency"
    assert by_name["entity"] == dict(by_name["entity"], fieldtype="Link", options="Entity")
    assert by_name["source"]["fieldtype"] == "Dynamic Link"
    assert by_name["source"]["options"] == "source_doctype"
    assert by_name["source_doctype"].get("hidden") == 1


# --- totals: the drill total is the statement line ------------------------

@pytest.mark.parametrize("rows, heading", [
    (F._heading1_rows(), "1"),           # BS: entities, eliminations, top-side, unknown layer
    (F._heading1_subcent_rows(), "1"),   # BS: per-row rounding -> a Rounding row
    (F._heading3_rows(), "3"),           # equity: CTA and current-year result
    (_pl_rows(), "4"),                   # P&L
])
def test_drill_total_equals_the_consolidated_statement_heading_amount(rows, heading):
    data = D.rows(_payload(rows, heading, F._TOPSIDE_JOURNALS), "USD")
    statement_row = next(r for r in R.rows(_statement_payload(rows)) if r.get("code") == heading)
    assert _total(data)["amount"] == statement_row["current"]
    layers = _layer_rows(data)
    assert round(sum(row["amount"] for row in layers), 2) == statement_row["current"]


def test_a_layer_row_per_drill_row_in_the_payloads_order():
    payload = _payload(F._heading1_rows(), "1", F._TOPSIDE_JOURNALS)
    layers = _layer_rows(D.rows(payload, "USD"))
    assert [row["layer"] for row in layers] == [row["layer"] for row in payload["drill"]["rows"]]
    assert [row["amount"] for row in layers] == [row["amount"] for row in payload["drill"]["rows"]]


def test_entity_rows_are_labelled_and_keyed_by_entity():
    layers = _layer_rows(D.rows(_payload(F._heading1_rows(), "1"), "USD"))
    entities = [row for row in layers if row["layer"] == "entity"]
    assert [(row["label"], row["entity"]) for row in entities] == [
        ("ZZA", "ZZA"), ("ZZB", "ZZB"), ("ZZC", "ZZC")]
    others = [row for row in layers if row["layer"] != "entity"]
    assert all(row["entity"] is None for row in others)
    assert "Intercompany eliminations" in [row["label"] for row in others]


def test_cta_and_current_year_result_are_their_own_layer_rows():
    layers = _layer_rows(D.rows(_payload(F._heading3_rows(), "3"), "USD"))
    by_layer = {row["layer"]: row for row in layers}
    assert by_layer["cta"]["label"] == "Currency translation (CTA)"
    assert by_layer["cta"]["amount"] == 8.0
    assert by_layer["current_year_result"]["amount"] == 241.43


def test_a_rounding_row_is_shown_when_the_payload_has_one():
    layers = _layer_rows(D.rows(_payload(F._heading1_subcent_rows(), "1"), "USD"))
    rounding = [row for row in layers if row["layer"] == "rounding"]
    assert len(rounding) == 1 and rounding[0]["amount"] == 0.02
    assert rounding[0]["label"] == "Rounding"


def test_accounts_follow_their_layer_at_indent_1_in_the_payloads_order():
    payload = _payload(F._heading1_rows(), "1")
    data = D.rows(payload, "USD")
    at = next(i for i, row in enumerate(data) if row["entity"] == "ZZA" and row["indent"] == 0)
    zza = next(r for r in payload["drill"]["rows"] if r["entity"] == "ZZA")
    children = data[at + 1: at + 1 + len(zza["accounts"])]
    assert [row["main_account"] for row in children] == [a["main_account"] for a in zza["accounts"]]
    assert all(row["indent"] == 1 for row in children)
    assert children[0]["label"] == "1110 Cash"
    assert [row["amount"] for row in children] == [a["amount"] for a in zza["accounts"]]


def test_every_row_carries_the_currency():
    data = D.rows(_payload(F._heading1_rows(), "1", F._TOPSIDE_JOURNALS), "USD")
    assert data and all(row["currency"] == "USD" for row in data)


# --- sources ----------------------------------------------------------------

_TBS = [
    {"name": "TBS-ZZA-2025-P7-001", "data_area_id": "ZZA", "fiscal_year": 2025, "fiscal_period": 7},
    {"name": "TBS-ZZB-2025-P7-001", "data_area_id": "ZZB", "fiscal_year": 2025, "fiscal_period": 7},
    {"name": "TBS-ZZB-2025-P6-001", "data_area_id": "ZZB", "fiscal_year": 2025, "fiscal_period": 6},
]


def test_an_entity_row_links_its_submitted_trial_balance_for_the_sources_period():
    layers = _layer_rows(D.rows(_payload(F._heading1_rows(), "1"), "USD", _TBS))
    by_entity = {row["entity"]: row for row in layers if row["layer"] == "entity"}
    assert by_entity["ZZA"]["source_doctype"] == "Trial Balance Submission"
    assert by_entity["ZZA"]["source"] == "TBS-ZZA-2025-P7-001"
    assert by_entity["ZZB"]["source"] == "TBS-ZZB-2025-P7-001"
    # No submission for ZZC: no link, never a guessed one.
    assert by_entity["ZZC"]["source"] is None and by_entity["ZZC"]["source_doctype"] is None


def test_two_submitted_trial_balances_for_one_entity_link_neither_and_the_message_says_so():
    tbs = _TBS + [{"name": "TBS-ZZA-2025-P7-002", "data_area_id": "ZZA",
                   "fiscal_year": 2025, "fiscal_period": 7}]
    payload = _payload(F._heading1_rows(), "1")
    zza = next(r for r in _layer_rows(D.rows(payload, "USD", tbs)) if r["entity"] == "ZZA")
    assert zza["source"] is None
    text = D.message(payload, tbs)
    assert "ZZA" in text and "TBS-ZZA-2025-P7-001" in text and "TBS-ZZA-2025-P7-002" in text


def test_the_cta_row_and_rows_without_a_source_link_nothing():
    layers = _layer_rows(D.rows(_payload(F._heading3_rows(), "3"), "USD", _TBS))
    for row in layers:
        if row["layer"] != "entity":
            assert row["source"] is None and row["source_doctype"] is None


def test_top_side_journals_are_listed_under_their_row_linking_each_journal():
    payload = _payload(F._heading1_rows(), "1", F._TOPSIDE_JOURNALS)
    data = D.rows(payload, "USD")
    at = next(i for i, row in enumerate(data) if row["label"] == "Top-side journals")
    after = data[at + 1:]
    group = next(row for row in after if row.get("is_journals"))
    assert group["indent"] == 1 and group["amount"] is None
    assert "posted in this period" in group["label"]
    journal = after[after.index(group) + 1]
    assert journal["indent"] == 2
    assert journal["source_doctype"] == "Consolidation Journal"
    assert journal["source"] == "J-1"
    assert journal["amount"] == 40.0
    assert "Reclass intercompany loan" in journal["label"]


def test_an_auto_reversal_journal_is_labelled_as_one():
    journals = [dict(F._TOPSIDE_JOURNALS[0], adjustment_type="auto_reversal")]
    rows = [dict(r, adjustment_type="auto_reversal") if r["adjustment_type"] == "topside" else r
            for r in F._heading1_rows()]
    data = D.rows(_payload(rows, "1", journals), "USD")
    journal = next(row for row in data if row.get("source") == "J-1")
    assert "reversal" in journal["label"].lower()


def test_an_unknown_source_kind_raises_naming_it():
    payload = _payload(F._heading1_rows(), "1")
    payload["drill"]["rows"][0]["source"] = {"kind": "zz_mystery"}
    with pytest.raises(ValueError, match="zz_mystery"):
        D.rows(payload, "USD")


# --- refusal and message ----------------------------------------------------

@pytest.mark.parametrize("state, message", [
    ("not_built", "The warehouse table is not built yet."),
    ("error", "1: drill total 1 does not match the statement line's current 2"),
])
def test_a_non_ok_drill_raises_its_own_message_never_an_empty_table(state, message):
    with pytest.raises(ValueError) as exc:
        D.rows({"state": state, "message": message, "drill": None}, "USD")
    assert str(exc.value) == message


def test_a_non_ok_drill_with_no_message_names_its_state():
    with pytest.raises(ValueError, match="not_built"):
        D.rows({"state": "not_built", "message": None, "drill": None}, "USD")


def test_message_names_period_group_heading_and_the_dimensions_note():
    text = D.message(_payload(F._heading1_rows(), "1"))
    assert "FY2025 P07" in text and "ZZ_GROUP" in text
    assert "1 ASSETS" in text
    assert DM.DIMENSIONS_NOTE in text


def test_message_is_html_escaped():
    payload = _payload(F._heading1_rows(), "1")
    payload["consolidation_group"] = "ZZ<b>G</b>"
    text = D.message(payload)
    assert "<b>" not in text and "ZZ&lt;b&gt;G&lt;/b&gt;" in text


def _entity_payload():
    payload = _payload(F._heading1_rows(), "1")
    payload.update({"basis": "Entity", "entity": "ZZA", "entity_name": "Zz Entity",
                    "consolidation_group": None})
    return payload


def test_entity_basis_message_names_basis_and_entity_not_a_group():
    first = D.message(_entity_payload()).split("<br>")[0]
    assert first.startswith("Statement drill · FY2025 P07 · Basis: Entity · ZZA Zz Entity · 1 ")


def test_entity_basis_message_without_entity_name_shows_the_code_alone():
    payload = _entity_payload()
    payload["entity_name"] = None
    assert "Basis: Entity · ZZA · 1 " in D.message(payload)


def test_group_message_header_is_unchanged_and_has_no_basis():
    first = D.message(_payload(F._heading1_rows(), "1")).split("<br>")[0]
    assert first.startswith("Statement drill · FY2025 P07 · ZZ_GROUP · 1 ")
    assert "Basis" not in first


# --- the Budget basis (#348 S5 K10a) --------------------------------------

_BUDGET_META = {"cycle": F.CYCLE, "layers": ["Original", "Revision 1"], "rate_set": "ZZRS",
                "scenario_id": "ZZSC", "declaration": "ZZDECL"}


def _budget_payload(heading="1", scoped=None):
    rows = F._budget_rows()
    line = F._statement_line(rows, heading)
    drill = DM.with_rates(
        DM.drill(rows, [], F._accounts(), heading, _keys(heading), F._declared(), scoped, line,
                 minor_unit=2),
        F._RATES, F.CYCLE)
    return {"period": dict(_PERIOD), "consolidation_group": "ZZ_GROUP", "heading": heading,
            "basis": "Budget", "meta": dict(_BUDGET_META), "state": "ok", "message": None,
            "drill": drill}


def _unbalanced_payload(scoped=None):
    rows = F._budget_rows()
    line = F._unbalanced_line(rows)
    drill = DM.unbalanced_drill(rows, F._KEYS, scoped, 2, cycle=F.CYCLE, statement_line=line)
    return {"period": dict(_PERIOD), "consolidation_group": "ZZ_GROUP", "heading": "UNBALANCED",
            "basis": "Budget", "meta": dict(_BUDGET_META), "state": "ok", "message": None,
            "drill": drill}


def test_budget_columns_gain_rate_and_rate_set_after_amount():
    cols = D.columns(2, "Budget")
    names = [c["fieldname"] for c in cols]
    assert names == ["label", "entity", "main_account", "amount", "rate", "rate_set",
                     "source_doctype", "source", "currency"]
    by_name = {c["fieldname"]: c for c in cols}
    # PR #554 review m6: the method is the chart's DECLARED Translation Method
    # as recorded at build time, not the one the core applied (closing can be
    # applied before a historical account's first tranche), so the column says so.
    assert (by_name["rate"]["label"], by_name["rate"]["fieldtype"], by_name["rate"]["width"]) == (
        "Declared method and rate", "Data", 200)
    assert (by_name["rate_set"]["label"], by_name["rate_set"]["fieldtype"],
            by_name["rate_set"]["options"], by_name["rate_set"]["width"]) == (
        "Rate Set", "Link", "Rate Set", 110)
    assert by_name["source"]["fieldtype"] == "Dynamic Link"
    assert by_name["amount"]["precision"] == 2


@pytest.mark.parametrize("basis", [None, "Group", "Entity"])
def test_other_bases_keep_their_columns(basis):
    assert D.columns(2, basis) == D.columns(2)
    assert [c["fieldname"] for c in D.columns(2, basis)] == [
        "label", "entity", "main_account", "amount", "source_doctype", "source", "currency"]


def test_budget_rows_show_rates_on_account_and_entity_rows():
    data = D.rows(_budget_payload(), "USD")
    zza = next(r for r in data if r["entity"] == "ZZA" and r["indent"] == 0)
    assert zza["rate"] == "closing 1.1; closing 1.12"
    assert zza["rate_set"] == "ZZRS"
    account = data[data.index(zza) + 1]
    assert account["indent"] == 1 and account["main_account"] == "1110"
    assert account["rate"] == "closing 1.1; closing 1.12" and account["rate_set"] == "ZZRS"
    zzb = next(r for r in data if r["entity"] == "ZZB" and r["indent"] == 0)
    assert zzb["rate"] == "closing 1.1"


def test_budget_total_and_rows_without_rates_show_none():
    data = D.rows(_budget_payload("3"), "USD")
    for row in data:
        assert row["rate"] is None and row["rate_set"] is None
    assert _total(data)["amount"] == 1005.0


def test_budget_outside_scope_aggregate_has_no_rate():
    data = D.rows(_budget_payload(scoped={"ZZA"}), "USD")
    outside = next(r for r in data if r["label"] == "1 entities outside your scope")
    assert outside["rate"] is None and outside["rate_set"] is None
    assert outside["budget_entries_filters"] is None


def test_a_blank_fx_method_reads_closing_undeclared():
    """PR #554 review m6: an account whose Translation Method the chart left
    blank is translated at closing by the consolidation core, so a blank
    ``fx_method`` reads "closing (undeclared)", never "chart default"."""
    payload = _budget_payload()
    zzb = next(r for r in payload["drill"]["rows"] if r["entity"] == "ZZB")
    zzb["rates"] = [{"fx_method": None, "rate": 1.25, "rate_set": "ZZRS"},
                    {"fx_method": "", "rate": 1.3, "rate_set": "ZZRS2"}]
    data = D.rows(payload, "USD")
    row = next(r for r in data if r["entity"] == "ZZB" and r["indent"] == 0)
    assert row["rate"] == "closing (undeclared) 1.25; closing (undeclared) 1.3"
    assert row["rate_set"] == "ZZRS; ZZRS2"
    assert "chart default" not in open(os.path.join(APP_DIR, "close", "statement_drill_report_model.py")).read()


def test_budget_entity_row_links_its_cycle_and_the_budget_entry_list():
    data = D.rows(_budget_payload(), "USD")
    zza = next(r for r in data if r["entity"] == "ZZA" and r["indent"] == 0)
    assert (zza["source_doctype"], zza["source"]) == ("Budget Cycle", F.CYCLE)
    assert zza["budget_entries_filters"] == {"cycle": F.CYCLE, "data_area_id": "ZZA"}
    for row in data:
        if row is not zza and row["entity"] != "ZZB":
            assert row["budget_entries_filters"] is None
        assert "budget_entries_filters" in row


def test_a_budget_entity_with_accounts_but_no_rates_raises_naming_it():
    rows = F._budget_rows()
    line = F._statement_line(rows, "1")
    drill = DM.drill(rows, [], F._accounts(), "1", F._KEYS, F._declared(), None, line, minor_unit=2)
    for item in drill["rows"]:
        item["source"] = {"kind": "cycle", "cycle": F.CYCLE, "entity": item["entity"]}
    payload = _budget_payload()
    payload["drill"] = drill
    with pytest.raises(ValueError, match="ZZA"):
        D.rows(payload, "USD")


def test_unbalanced_drill_lays_out_one_row_per_entity_then_total():
    data = D.rows(_unbalanced_payload(), "USD")
    assert [(r["label"], r["indent"], r["amount"]) for r in data] == [
        ("ZZA", 0, 705.0), ("ZZB", 0, -200.0), ("Total", 0, 505.0)]
    assert _total(data)["amount"] == F._unbalanced_line(F._budget_rows())["current"] == 505.0
    for row in data[:2]:
        assert (row["source_doctype"], row["source"]) == ("Budget Cycle", F.CYCLE)
        assert row["rate"] is None and row["rate_set"] is None
    assert data[0]["budget_entries_filters"] == {"cycle": F.CYCLE, "data_area_id": "ZZA"}
    assert data[2]["budget_entries_filters"] is None


def test_unbalanced_drill_outside_scope_row():
    data = D.rows(_unbalanced_payload({"ZZA"}), "USD")
    assert [r["label"] for r in data] == ["ZZA", "1 entities outside your scope", "Total"]
    assert data[1]["budget_entries_filters"] is None and data[1]["source"] is None


def test_budget_message_header_and_budget_line():
    lines = D.message(_budget_payload()).split("<br>")
    assert lines[0] == ("Statement drill · FY2025 P07 · Budget · ZZ_GROUP · "
                        "1 " + _budget_payload()["drill"]["heading_name"]
                        + " (" + _budget_payload()["drill"]["section"] + ")")
    assert lines[1] == (f"Budget Cycle {F.CYCLE}, Original, Revision 1, Rate Set ZZRS; "
                        "before intercompany eliminations and without group-level adjustments")
    assert lines[2] == _budget_payload()["drill"]["dimensions_note"]
    assert "#" not in "".join(lines)


def test_unbalanced_message_header():
    header = D.message(_unbalanced_payload()).split("<br>")[0]
    assert header == ("Statement drill · FY2025 P07 · Budget · ZZ_GROUP · "
                      "UNBALANCED Unbalanced budget (no balancing entry) (Balance Sheet)")


def test_other_bases_gain_no_budget_keys():
    for row in D.rows(_payload(F._heading1_rows(), "1", F._TOPSIDE_JOURNALS), "USD"):
        assert not {"rate", "rate_set", "budget_entries_filters"} & set(row)
    assert "Budget" not in D.message(_payload(F._heading1_rows(), "1"))

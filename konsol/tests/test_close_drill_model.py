"""Drill model, pure: konsol/close/drill_model.py (konsol#305 Wave 4, N50;
stories 8.2; D2-5 — eliminations, CTA, topsides, equity method and deal
journals are their own rows, never under an entity, regardless of
``data_area_id``; konsolidat#245 — no dimension breakdown).

Loaded by path; the module imports nothing from frappe or konsol.
``statement_model`` and ``close_policy_model`` are also loaded by path so
``statement_line`` fixtures come from the REAL producer
(``statement_model.statement``), never a hand-built dict that might differ
from what it actually returns (wave-3/4 lesson, R31).
"""
import ast
import importlib.util
import os

import pytest

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_PATH = os.path.join(APP_DIR, "close", "drill_model.py")
_spec = importlib.util.spec_from_file_location("drill_model_under_test", _PATH)
M = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(M)

_SM_PATH = os.path.join(APP_DIR, "close", "statement_model.py")
_sm_spec = importlib.util.spec_from_file_location("statement_model_under_test_for_drill", _SM_PATH)
SM = importlib.util.module_from_spec(_sm_spec)
_sm_spec.loader.exec_module(SM)

_CPM_PATH = os.path.join(APP_DIR, "close", "close_policy_model.py")
_cpm_spec = importlib.util.spec_from_file_location("close_policy_model_under_test_for_drill", _CPM_PATH)
CPM = importlib.util.module_from_spec(_cpm_spec)
_cpm_spec.loader.exec_module(CPM)


def _calendar(years):
    """Mirror test_close_statement_model.py's ``_calendar``: Opening P0,
    Regular P1-P12, Closing P13 for each fiscal year."""
    rows = []
    for fiscal_year in years:
        rows.append({"fiscal_year": fiscal_year, "fiscal_period": 0, "period_type": "Opening"})
        for fiscal_period in range(1, 13):
            rows.append({"fiscal_year": fiscal_year, "fiscal_period": fiscal_period, "period_type": "Regular"})
        rows.append({"fiscal_year": fiscal_year, "fiscal_period": 13, "period_type": "Closing"})
    return rows


_CALENDAR = _calendar((2024, 2025))
_KEY = (2025, 7)
_KEYS = SM._keys_up_to(_CALENDAR, _KEY)  # the real BS cumulative window (N48/N49)

#: #305-W4-1: the two declared statement accounts, both Published Balance
#: Sheet leaves under heading 3 (EQUITY) — same shape as
#: test_close_statement_model.py's ``_S_DECLARED_ROWS``.
_DECLARED_ROWS = {
    "3300": {"is_group": False, "status": "Published", "statement_section": SM.BS,
              "account_name": "AOCI - CTA", "parent_account": "3"},
    "3100": {"is_group": False, "status": "Published", "statement_section": SM.BS,
              "account_name": "Retained earnings", "parent_account": "3"},
}


def _declared(cta_account="3300", result_account="3100"):
    return CPM.statement_accounts(cta_account, result_account, _DECLARED_ROWS)


def _accounts():
    """ASSETS (1, Debit) and EQUITY (3, Credit) on the Balance Sheet;
    COST OF SALES (4) on the Profit and Loss (recon: the live shape, as
    test_close_statement_model.py's ``_s_accounts``)."""
    return {
        "1": {"account_name": "ASSETS", "parent_account": None, "is_group": True,
              "statement_section": SM.BS, "lft": 1, "normal_balance": "Debit"},
        "1110": {"account_name": "Cash", "parent_account": "1", "is_group": False,
                  "statement_section": SM.BS, "lft": 2},
        "1120": {"account_name": "Receivables", "parent_account": "1", "is_group": False,
                  "statement_section": SM.BS, "lft": 3},
        "3": {"account_name": "EQUITY", "parent_account": None, "is_group": True,
              "statement_section": SM.BS, "lft": 9, "normal_balance": "Credit"},
        "3200": {"account_name": "Share capital", "parent_account": "3", "is_group": False,
                  "statement_section": SM.BS, "lft": 10},
        "3300": {"account_name": "AOCI - CTA", "parent_account": "3", "is_group": False,
                  "statement_section": SM.BS, "lft": 11},
        "3100": {"account_name": "Retained earnings", "parent_account": "3", "is_group": False,
                  "statement_section": SM.BS, "lft": 12},
        "4": {"account_name": "COST OF SALES", "parent_account": None, "is_group": True,
              "statement_section": SM.PL, "lft": 13},
        "4100": {"account_name": "Net sales", "parent_account": "4", "is_group": False,
                  "statement_section": SM.PL, "lft": 14},
    }


def _row(main_account, amount, adjustment_type, data_area_id, null_rows=0,
         fiscal_year=_KEY[0], fiscal_period=_KEY[1]):
    return {
        "fiscal_year": fiscal_year, "fiscal_period": fiscal_period,
        "data_area_id": data_area_id, "main_account": main_account,
        "adjustment_type": adjustment_type, "amount": amount, "null_rows": null_rows,
    }


def _statement_line(rows, heading, accounts=None, declared=None):
    """The REAL statement line for ``heading`` (R31: feed the real
    producer, never a hand-built dict)."""
    accounts = accounts or _accounts()
    declared = declared if declared is not None else _declared()
    result = SM.statement(rows, accounts, _CALENDAR, _KEY, declared, minor_unit=2)
    section_index = 1 if accounts[heading]["statement_section"] == SM.BS else 0
    for line in result["sections"][section_index]["lines"]:
        if line.get("heading") == heading:
            return line
    raise AssertionError(f"no statement line for heading {heading!r}")


def _by_layer(result, layer):
    return [r for r in result["rows"] if r["layer"] == layer]


# --- heading "1": entity split, D2-5 exclusion, unknown layer -------------

def _heading1_rows():
    """3 entities on 1110/1120 under heading 1; an IC elimination (group
    view, data_area_id ''); an NCI elimination carrying ZZB's
    data_area_id; a topside journal carrying ZZA's data_area_id; an
    unrecognised adjustment_type carrying ZZA's data_area_id too (D2-5:
    none of these belong under their entity's row)."""
    return [
        _row("1110", 300.0, "entity", "ZZA"),
        _row("1110", 200.0, "entity", "ZZB"),
        _row("1120", 100.0, "entity", "ZZC"),
        _row("1110", -50.0, "ic_elimination", ""),
        _row("1110", 40.0, "topside", "ZZA"),
        _row("1110", 7.0, "zz_new_layer", "ZZA"),
    ]


_TOPSIDE_JOURNALS = [
    {"journal_id": "J-1", "adjustment_type": "topside", "data_area_id": "ZZA",
     "main_account": "1110", "net_amount": 40.0, "description": "Reclass intercompany loan",
     "posted_by": "alice@example.com", "approved_by": "bob@example.com"},
]


def test_entity_rows_exclude_topside_amounts_with_the_same_entity_d2_5():
    """Failure path (D2-5): the ZZA entity row's amount excludes the
    unknown-layer and topside amounts that also carry data_area_id "ZZA".
    (The NCI elimination layer that once carried ZZB's data_area_id is
    retired: intercompany eliminations are posted in full since
    konsolidat#204 (a), konsol#117 pair 2.)"""
    rows = _heading1_rows()
    line = _statement_line(rows, "1")
    result = M.drill(rows, _TOPSIDE_JOURNALS, _accounts(), "1", _KEYS, _declared(), None, line, minor_unit=2)

    zza = _by_layer(result, "entity")
    zza_row = next(r for r in zza if r["entity"] == "ZZA")
    assert zza_row["amount"] == 300.0

    zzb_row = next(r for r in zza if r["entity"] == "ZZB")
    assert zzb_row["amount"] == 200.0

    zzc_row = next(r for r in zza if r["entity"] == "ZZC")
    assert zzc_row["amount"] == 100.0


def test_cta_ic_eliminations_and_topside_are_each_one_row_with_entity_none():
    rows = _heading1_rows()
    line = _statement_line(rows, "1")
    result = M.drill(rows, _TOPSIDE_JOURNALS, _accounts(), "1", _KEYS, _declared(), None, line, minor_unit=2)

    ic_rows = [r for r in result["rows"] if r["label"] == "Intercompany eliminations"]
    assert len(ic_rows) == 1
    assert ic_rows[0]["entity"] is None
    assert ic_rows[0]["amount"] == -50.0  # the one IC layer; ic_elimination_nci retired (konsolidat#204 (a), konsol#117 pair 2)

    topside_rows = [r for r in result["rows"] if r["label"] == "Top-side journals"]
    assert len(topside_rows) == 1
    assert topside_rows[0]["entity"] is None
    assert topside_rows[0]["amount"] == 40.0


def test_unknown_adjustment_type_is_its_own_row_labelled_with_the_raw_code():
    rows = _heading1_rows()
    line = _statement_line(rows, "1")
    result = M.drill(rows, _TOPSIDE_JOURNALS, _accounts(), "1", _KEYS, _declared(), None, line, minor_unit=2)

    unknown_rows = _by_layer(result, "zz_new_layer")
    assert len(unknown_rows) == 1
    assert unknown_rows[0]["label"] == "zz_new_layer"
    assert unknown_rows[0]["entity"] is None
    assert unknown_rows[0]["amount"] == 7.0


def test_drill_total_equals_the_real_statement_lines_current():
    rows = _heading1_rows()
    line = _statement_line(rows, "1")
    result = M.drill(rows, _TOPSIDE_JOURNALS, _accounts(), "1", _KEYS, _declared(), None, line, minor_unit=2)
    assert result["total"] == line["current"]
    assert result["total"] == 597.0  # 300+200+100-50+40+7


def test_tampering_a_row_after_the_statement_was_built_raises():
    """Failure path: the drill and the statement must agree. A mismatch is
    a bug, never shown as a silent difference."""
    rows = _heading1_rows()
    line = _statement_line(rows, "1")
    tampered = [dict(r) for r in rows]
    tampered[0]["amount"] = 999.0
    with pytest.raises(ValueError, match="does not match"):
        M.drill(tampered, _TOPSIDE_JOURNALS, _accounts(), "1", _KEYS, _declared(), None, line, minor_unit=2)


def _heading1_subcent_rows():
    """S1 (W4 server review): 5 entities with 3-decimal sub-cent tails, the
    live shape (ClickHouse Float64 sums leave a sub-cent tail). Each
    entity's own amount rounds DOWN individually (100.004 -> 100.00,
    losing 0.004 each, 0.02 over the 5), but the statement line's total is
    rounded once, from the raw 500.02 — so the per-row-rounded sum (500.00)
    must never be compared to the statement's figure directly."""
    return [
        _row("1110", 100.004, "entity", "ZZA"),
        _row("1110", 100.004, "entity", "ZZB"),
        _row("1110", 100.004, "entity", "ZZC"),
        _row("1110", 100.004, "entity", "ZZD"),
        _row("1110", 100.004, "entity", "ZZE"),
    ]


def test_drill_total_matches_statement_despite_per_entity_sub_cent_rounding():
    """S1: the drill must compare at the raw (unrounded) level, quantized
    once, against the statement line's own once-rounded total — never sum
    each already-rounded display row and compare that. The cents lost to
    per-row rounding show up as a visible "rounding" row, never an error."""
    rows = _heading1_subcent_rows()
    line = _statement_line(rows, "1")
    assert line["current"] == 500.02  # the real producer, rounded once

    result = M.drill(rows, [], _accounts(), "1", _KEYS, _declared(), None, line, minor_unit=2)

    assert result["total"] == line["current"] == 500.02

    entity_rows = _by_layer(result, "entity")
    assert len(entity_rows) == 5
    assert sum(r["amount"] for r in entity_rows) == 500.00  # each rounds to 100.00

    rounding_rows = _by_layer(result, "rounding")
    assert len(rounding_rows) == 1
    assert rounding_rows[0]["amount"] == 0.02
    assert rounding_rows[0]["entity"] is None
    assert rounding_rows[0]["accounts"] == []
    assert rounding_rows[0]["source"] is None


def test_no_rounding_row_when_display_rows_already_sum_exactly():
    rows = _heading1_rows()
    line = _statement_line(rows, "1")
    result = M.drill(rows, _TOPSIDE_JOURNALS, _accounts(), "1", _KEYS, _declared(), None, line, minor_unit=2)
    assert _by_layer(result, "rounding") == []


def test_scope_aggregates_entities_outside_allowed_with_no_codes_or_accounts():
    rows = _heading1_rows()
    line = _statement_line(rows, "1")
    result = M.drill(rows, _TOPSIDE_JOURNALS, _accounts(), "1", _KEYS, _declared(),
                      {"ZZA"}, line, minor_unit=2)

    entity_rows = _by_layer(result, "entity")
    assert len(entity_rows) == 2  # ZZA itself, plus one aggregate

    zza_row = next(r for r in entity_rows if r["entity"] == "ZZA")
    assert zza_row["amount"] == 300.0

    outside = next(r for r in entity_rows if r["entity"] is None)
    assert outside["label"] == "2 entities outside your scope"
    assert outside["accounts"] == []
    assert outside["amount"] == 300.0  # 200 (ZZB) + 100 (ZZC)

    for row in result["rows"]:
        assert row.get("entity") != "ZZB"
        assert row.get("entity") != "ZZC"
        assert "ZZB" not in (row.get("label") or "")
        assert "ZZC" not in (row.get("label") or "")

    # scope never changes the total the drill proves (W4-E9: group totals).
    assert result["total"] == line["current"]


def test_entity_row_source_is_tb_topside_row_source_is_journals():
    rows = _heading1_rows()
    line = _statement_line(rows, "1")
    result = M.drill(rows, _TOPSIDE_JOURNALS, _accounts(), "1", _KEYS, _declared(), None, line, minor_unit=2)

    zza_row = next(r for r in _by_layer(result, "entity") if r["entity"] == "ZZA")
    assert zza_row["source"]["kind"] == "tb"
    assert zza_row["source"]["entity"] == "ZZA"

    topside_row = next(r for r in result["rows"] if r["label"] == "Top-side journals")
    assert topside_row["source"] == {"kind": "journals"}
    assert topside_row["journals"][0]["journal_id"] == "J-1"
    assert topside_row["journals"][0]["amount"] == 40.0


def test_accounts_list_is_in_lft_order():
    rows = _heading1_rows() + [_row("1120", 5.0, "entity", "ZZA")]
    line = _statement_line(rows, "1")
    result = M.drill(rows, _TOPSIDE_JOURNALS, _accounts(), "1", _KEYS, _declared(), None, line, minor_unit=2)
    zza_row = next(r for r in _by_layer(result, "entity") if r["entity"] == "ZZA")
    codes = [a["main_account"] for a in zza_row["accounts"]]
    assert codes == ["1110", "1120"]  # lft 2 then lft 3


def test_dimensions_note_names_the_tracking_issue():
    rows = _heading1_rows()
    line = _statement_line(rows, "1")
    result = M.drill(rows, _TOPSIDE_JOURNALS, _accounts(), "1", _KEYS, _declared(), None, line, minor_unit=2)
    assert result["dimensions_note"] == "Not broken down by dimension yet."


# --- heading "3": CTA and current-year result, placed on the declared ----
# accounts' heading, never under an entity (D2-5) -------------------------

def _heading3_rows():
    return [
        _row("3200", -500.0, "entity", "ZZA"),     # share capital, entity-level
        _row("CTA", -5.0, "cta", "ZZA"),
        _row("CTA", -3.0, "cta", "ZZB"),
        _row("4100", -241.43, "entity", "ZZA"),    # P&L revenue, feeds current-year result
    ]


def test_cta_heading_drill_shows_the_cta_row_never_under_an_entity():
    rows = _heading3_rows()
    line = _statement_line(rows, "3")
    result = M.drill(rows, [], _accounts(), "3", _KEYS, _declared(), None, line, minor_unit=2)

    cta_rows = _by_layer(result, "cta")
    assert len(cta_rows) == 1
    assert cta_rows[0]["entity"] is None
    assert cta_rows[0]["amount"] == 8.0  # -1 * (-5 + -3), EQUITY is Credit (flipped)
    assert cta_rows[0]["accounts"] == []


def test_result_heading_drill_shows_the_current_year_result_row():
    rows = _heading3_rows()
    line = _statement_line(rows, "3")
    result = M.drill(rows, [], _accounts(), "3", _KEYS, _declared(), None, line, minor_unit=2)

    result_rows = _by_layer(result, "current_year_result")
    assert len(result_rows) == 1
    assert result_rows[0]["entity"] is None
    assert result_rows[0]["accounts"] == []
    assert result_rows[0]["amount"] == 241.43

    assert result["total"] == line["current"]
    assert result["total"] == 749.43  # 500 (entity) + 8 (cta) + 241.43 (result)


def test_undeclared_cta_account_means_no_cta_row_on_that_heading():
    """Failure path: when the CTA account is undeclared, drilling EQUITY
    never manufactures a CTA row (N49 never places it either)."""
    rows = [r for r in _heading3_rows() if r["adjustment_type"] != "cta"]
    declared = _declared(cta_account="")
    line = _statement_line(rows, "3", declared=declared)
    result = M.drill(rows, [], _accounts(), "3", _KEYS, declared, None, line, minor_unit=2)
    assert _by_layer(result, "cta") == []


# --- failure paths: null/None amounts are never read as 0 -----------------

def test_null_rows_raises_naming_the_account():
    rows = [_row("1110", 100.0, "entity", "ZZA", null_rows=1)]
    with pytest.raises(ValueError, match="1110"):
        M.drill(rows, [], _accounts(), "1", _KEYS, _declared(), None,
                {"heading": "1", "current": 0.0}, minor_unit=2)


def test_amount_none_raises_naming_the_account():
    rows = [_row("1110", None, "entity", "ZZA")]
    with pytest.raises(ValueError, match="1110"):
        M.drill(rows, [], _accounts(), "1", _KEYS, _declared(), None,
                {"heading": "1", "current": 0.0}, minor_unit=2)


# --- module contract --------------------------------------------------------

def test_module_imports_no_frappe():
    """Mirror test_close_statement_model.py / test_assertion_warn_amber.py:269."""
    with open(_PATH) as fh:
        tree = ast.parse(fh.read())
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            assert not any(a.name.split(".")[0] in ("frappe", "konsol") for a in node.names)
        if isinstance(node, ast.ImportFrom):
            assert (node.module or "").split(".")[0] not in ("frappe", "konsol")


# --- #305 story 6.5: an auto-reversal journal row is tagged in the drill ------

def test_a_journal_row_carries_an_auto_reversal_tag_true_only_for_auto_reversal():
    """gold_consolidation_adjustments keeps the original journal_id on its
    ``auto_reversal`` rows (V01), so the tag is what tells the reader this
    row is the reversal of an earlier period's journal, not a new one."""
    rows = _heading1_rows()
    line = _statement_line(rows, "1")
    journals = _TOPSIDE_JOURNALS + [
        {"journal_id": "J-P6", "adjustment_type": "auto_reversal", "data_area_id": "ZZA",
         "main_account": "1110", "net_amount": -10.0, "description": "June bonus",
         "posted_by": "alice@example.com", "approved_by": "bob@example.com"},
    ]
    result = M.drill(rows, journals, _accounts(), "1", _KEYS, _declared(), None, line, minor_unit=2)
    topside_row = next(r for r in result["rows"] if r["label"] == "Top-side journals")
    tags = {j["journal_id"]: j["auto_reversal"] for j in topside_row["journals"]}
    assert tags == {"J-1": False, "J-P6": True}


# --- a heading with no warehouse rows (konsol#305 8.5) ------------------------

def test_a_heading_with_no_rows_drills_to_zero_with_no_rows():
    """Found by the 8.5 export, which drills EVERY heading: a heading with
    no balances made ``sum()`` over no rows return the int 0, and
    ``_round`` raised AttributeError (a 500 from get_drill too)."""
    rows = [_row("3200", -100.0, "entity", "ZZA")]
    line = _statement_line(rows, "1")
    assert line["current"] == 0.0
    result = M.drill(rows, [], _accounts(), "1", _KEYS, _declared(), None, line, minor_unit=2)
    assert result["rows"] == []
    assert result["total"] == 0.0


def test_year_end_close_and_ownership_exit_have_labels():
    """konsolidat#316 / #335: the two new FCTB adjustment types are named."""
    assert M._LAYER_LABELS["journal_close"] == "Year-end close"
    assert M._LAYER_LABELS["ownership_exit"] == "Ownership exit"


# --- the budget drill (konsol#348 S5 K8; S5-Q3 a, S5-Q7 a) ------------------
#
# gold_fully_consolidated_budget rows have the FCTB shape plus a one-per-
# (entity, period) ``UNBALANCED`` plug (adjustment_type "unbalanced") and a
# ``CTA`` row. A heading drill over them must skip the plug (a code whose
# leaf is not under the heading is skipped, drill_model.py) and still match
# its statement line; ``_LAYER_LABELS`` must never gain an ``unbalanced``
# entry, or the plug would land under headings and break their totals. The
# "Unbalanced budget" line has its own drill, one row per entity, and the
# budget drill carries the rate each account was translated at (S5-Q7 a).

UNBALANCED_LABEL = "Unbalanced budget (no balancing entry)"
CYCLE = "BCYC-ZZB-2025-abc12345"


def _budget_rows():
    """Two entities; each (entity, period) sums to 0 with its plug."""
    return [
        _row("1110", 300.0, "entity", "ZZA"),
        _row("1110", 200.0, "entity", "ZZB"),
        _row("4100", -1000.0, "entity", "ZZA"),
        _row("CTA", -5.0, "cta", "ZZA"),
        _row("UNBALANCED", 705.0, "unbalanced", "ZZA"),
        _row("UNBALANCED", -200.0, "unbalanced", "ZZB"),
    ]


def _rate(entity, account, fx_method, rate, rate_set="ZZRS"):
    return {"data_area_id": entity, "main_account": account, "fx_method": fx_method,
            "translation_rate": rate, "rate_set": rate_set}


_RATES = [
    _rate("ZZA", "1110", "closing", 1.10),
    _rate("ZZA", "1110", "closing", 1.12),      # another period of the window
    _rate("ZZA", "1110", "closing", 1.10),      # a duplicate: distinct pairs only
    _rate("ZZB", "1110", "closing", 1.10),
    _rate("ZZA", "4100", "average", 1.08),      # not under heading 1
]


def test_the_layer_labels_never_gain_an_unbalanced_entry():
    assert "unbalanced" not in M._LAYER_LABELS
    assert "unbalanced" not in M._LAYER_KEY_BY_LABEL.values()


def test_a_heading_drill_over_budget_rows_skips_the_plug_and_matches_its_line():
    rows = _budget_rows()
    line = _statement_line(rows, "1")
    result = M.drill(rows, [], _accounts(), "1", _KEYS, _declared(), None, line, minor_unit=2)
    assert result["total"] == line["current"] == 500.0
    assert [r["layer"] for r in result["rows"]] == ["entity", "entity"]
    assert not any("UNBALANCED" in str(r) for r in result["rows"])
    # The CTA heading too: the plug is not a CTA either.
    line3 = _statement_line(rows, "3")
    result3 = M.drill(rows, [], _accounts(), "3", _KEYS, _declared(), None, line3, minor_unit=2)
    assert result3["total"] == line3["current"] == 1005.0   # result 1000 + CTA 5, Credit side
    assert _by_layer(result3, "cta")[0]["amount"] == 5.0


def _unbalanced_line(rows):
    result = SM.statement(rows, _accounts(), _CALENDAR, _KEY, _declared(), minor_unit=2)
    return next(line for line in result["sections"][1]["lines"] if line["kind"] == "unbalanced")


def test_unbalanced_drill_is_one_row_per_entity_in_net_debit_terms():
    rows = _budget_rows()
    result = M.unbalanced_drill(rows, _KEYS, None, minor_unit=2, cycle=CYCLE)
    assert result["heading"] == "UNBALANCED"
    assert result["heading_name"] == UNBALANCED_LABEL
    assert result["section"] == SM.BS
    assert [(r["layer"], r["entity"], r["amount"], r["accounts"]) for r in result["rows"]] == [
        ("entity", "ZZA", 705.0, []), ("entity", "ZZB", -200.0, [])]
    assert result["rows"][0]["source"] == {"kind": "cycle", "cycle": CYCLE, "entity": "ZZA"}
    assert result["total"] == 505.0
    assert result["minor_unit"] == 2 and result["dimensions_note"] == M.DIMENSIONS_NOTE


def test_unbalanced_drill_total_matches_the_statements_unbalanced_line_or_raises():
    rows = _budget_rows()
    line = _unbalanced_line(rows)
    assert line["current"] == 505.0
    assert M.unbalanced_drill(rows, _KEYS, None, 2, cycle=CYCLE, statement_line=line)["total"] == 505.0
    rows.append(_row("UNBALANCED", 1.0, "unbalanced", "ZZB"))
    with pytest.raises(ValueError, match="UNBALANCED"):
        M.unbalanced_drill(rows, _KEYS, None, 2, cycle=CYCLE, statement_line=line)


def test_unbalanced_drill_cuts_to_scope_without_naming_the_other_entity():
    result = M.unbalanced_drill(_budget_rows(), _KEYS, {"ZZA"}, 2, cycle=CYCLE)
    assert [(r["entity"], r["label"], r["amount"]) for r in result["rows"]] == [
        ("ZZA", None, 705.0), (None, "1 entities outside your scope", -200.0)]
    for row in result["rows"]:
        assert row.get("entity") != "ZZB" and "ZZB" not in (row.get("label") or "")
    assert result["total"] == 505.0


def test_unbalanced_drill_refuses_null_rows_and_ignores_other_layers_and_keys():
    rows = _budget_rows() + [_row("UNBALANCED", 99.0, "unbalanced", "ZZA", fiscal_year=2026, fiscal_period=1)]
    assert M.unbalanced_drill(rows, _KEYS, None, 2)["total"] == 505.0
    with pytest.raises(ValueError, match="UNBALANCED"):
        M.unbalanced_drill([_row("UNBALANCED", None, "unbalanced", "ZZA")], _KEYS, None, 2)


def test_with_rates_attaches_distinct_rates_per_account_and_the_cycle_source():
    rows = _budget_rows()
    line = _statement_line(rows, "1")
    drill = M.drill(rows, [], _accounts(), "1", _KEYS, _declared(), None, line, minor_unit=2)
    out = M.with_rates(drill, _RATES, CYCLE)
    zza = next(r for r in out["rows"] if r["entity"] == "ZZA")
    assert zza["accounts"][0]["rates"] == [
        {"fx_method": "closing", "rate": 1.10, "rate_set": "ZZRS"},
        {"fx_method": "closing", "rate": 1.12, "rate_set": "ZZRS"},
    ]
    assert zza["rates"] == zza["accounts"][0]["rates"]
    assert zza["source"] == {"kind": "cycle", "cycle": CYCLE, "entity": "ZZA"}
    zzb = next(r for r in out["rows"] if r["entity"] == "ZZB")
    assert zzb["rates"] == [{"fx_method": "closing", "rate": 1.10, "rate_set": "ZZRS"}]
    assert out["total"] == 500.0, "rates change no amount"


def test_with_rates_refuses_an_entity_account_with_no_rate_row_never_blank():
    rows = _budget_rows()
    line = _statement_line(rows, "1")
    drill = M.drill(rows, [], _accounts(), "1", _KEYS, _declared(), None, line, minor_unit=2)
    with pytest.raises(ValueError, match=r"ZZB.*1110"):
        M.with_rates(drill, [r for r in _RATES if r["data_area_id"] != "ZZB"], CYCLE)


def test_with_rates_leaves_non_entity_rows_and_the_scope_aggregate_alone():
    rows = _budget_rows()
    line3 = _statement_line(rows, "3")
    drill3 = M.drill(rows, [], _accounts(), "3", _KEYS, _declared(), None, line3, minor_unit=2)
    out = M.with_rates(drill3, _RATES, CYCLE)
    for row in out["rows"]:
        assert row["layer"] != "entity" and "rates" not in row
    line = _statement_line(rows, "1")
    scoped = M.drill(rows, [], _accounts(), "1", _KEYS, _declared(), {"ZZA"}, line, minor_unit=2)
    out = M.with_rates(scoped, _RATES, CYCLE)
    outside = next(r for r in out["rows"] if r["entity"] is None)
    assert "rates" not in outside and outside["source"] is None

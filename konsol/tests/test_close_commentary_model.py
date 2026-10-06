"""Commentary model, pure: konsol/close/commentary_model.py (konsol#305 M42;
stories 8.3, 9.1; #305-W4-5 5b, W4-6 6a; D2-4; W4-E14, W4-E15, W4-E16).

Loaded by path; the module imports nothing from frappe or konsol.
"""
import ast
import importlib.util
import os

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODEL_PATH = os.path.join(APP_DIR, "close", "commentary_model.py")
_spec = importlib.util.spec_from_file_location("commentary_model_under_test", MODEL_PATH)
M = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(M)


def _period(status="Open", fiscal_year=2025, fiscal_period=7):
    return {"fiscal_year": fiscal_year, "fiscal_period": fiscal_period, "status": status}


def _heading_row(is_group=1, status="Published", account_name="Revenue"):
    return {"is_group": is_group, "status": status, "account_name": account_name}


# --- save_problems: period --------------------------------------------------

def test_open_period_with_a_published_heading_and_root_group_passes():
    problems = M.save_problems(_period("Open"), "4000", _heading_row(), "ZZGRP", True)
    assert problems == []


def test_closed_period_is_refused_naming_w4_5():
    problems = M.save_problems(_period("Closed"), "4000", _heading_row(), "ZZGRP", True)
    assert problems == [
        "FY2025 P07 is Closed: commentary is refused once a period is closed "
        "(#305-W4-5). Reopen the period to change its commentary."
    ]


def test_locked_period_is_refused_the_same_way():
    problems = M.save_problems(_period("Locked"), "4000", _heading_row(), "ZZGRP", True)
    assert problems == [
        "FY2025 P07 is Locked: commentary is refused once a period is closed "
        "(#305-W4-5). Reopen the period to change its commentary."
    ]


def test_signed_open_period_still_passes():
    # W4-5 5b: commentary is allowed while Open, signed or not.
    period = _period("Open")
    period["signed_off"] = True
    problems = M.save_problems(period, "4000", _heading_row(), "ZZGRP", True)
    assert problems == []


# --- save_problems: heading --------------------------------------------------

def test_a_leaf_account_is_refused_naming_d2_4():
    problems = M.save_problems(_period("Open"), "4000", _heading_row(is_group=0), "ZZGRP", True)
    assert problems == [
        "4000 is not a statement heading of the group chart: commentary attaches "
        "to a heading (Main Account with Is Group), D2-4."
    ]


def test_a_draft_heading_is_refused():
    problems = M.save_problems(_period("Open"), "4000", _heading_row(status="Draft"), "ZZGRP", True)
    assert problems == [
        "4000 is not a statement heading of the group chart: commentary attaches "
        "to a heading (Main Account with Is Group), D2-4."
    ]


def test_no_such_heading_is_refused():
    problems = M.save_problems(_period("Open"), "4000", None, "ZZGRP", True)
    assert problems == [
        "4000 is not a statement heading of the group chart: commentary attaches "
        "to a heading (Main Account with Is Group), D2-4."
    ]


# --- save_problems: group ----------------------------------------------------

def test_a_non_root_group_is_refused():
    problems = M.save_problems(_period("Open"), "4000", _heading_row(), "ZZENT", False)
    assert problems == ["ZZENT is not a consolidation group."]


# --- save_problems: more than one problem at once ----------------------------

def test_every_problem_is_reported_together():
    problems = M.save_problems(_period("Closed"), "4000", None, "ZZENT", False)
    assert problems == [
        "FY2025 P07 is Closed: commentary is refused once a period is closed "
        "(#305-W4-5). Reopen the period to change its commentary.",
        "4000 is not a statement heading of the group chart: commentary attaches "
        "to a heading (Main Account with Is Group), D2-4.",
        "ZZENT is not a consolidation group.",
    ]


# --- stale_problem ------------------------------------------------------------

def test_first_save_has_no_token_on_either_side():
    assert M.stale_problem(None, None, "zz-dana@example.com", "2026-10-04T12:00:00+00:00") is None


def test_matching_tokens_are_not_stale():
    assert M.stale_problem("2026-10-04T12:00:00", "2026-10-04T12:00:00",
                            "zz-dana@example.com", "2026-10-04T12:00:00+00:00") is None


def test_a_different_token_names_the_editor_and_time():
    result = M.stale_problem("2026-10-04T12:00:00", "2026-10-04T12:05:00",
                              "zz-dana@example.com", "2026-10-04T12:05:00+00:00")
    assert result == (
        "zz-dana@example.com changed this commentary at 2026-10-04T12:05:00+00:00: "
        "reload it and edit again."
    )


def test_a_sent_token_for_a_document_that_does_not_exist_is_stale():
    result = M.stale_problem("2026-10-04T12:00:00", None,
                              "zz-dana@example.com", "2026-10-04T12:05:00+00:00")
    assert result == (
        "zz-dana@example.com changed this commentary at 2026-10-04T12:05:00+00:00: "
        "reload it and edit again."
    )


def test_no_token_for_a_document_that_exists_is_stale():
    result = M.stale_problem(None, "2026-10-04T12:00:00",
                              "zz-dana@example.com", "2026-10-04T12:05:00+00:00")
    assert result == (
        "zz-dana@example.com changed this commentary at 2026-10-04T12:05:00+00:00: "
        "reload it and edit again."
    )


# --- event_detail -------------------------------------------------------------

def test_event_detail_keys_are_exactly_the_four():
    detail = M.event_detail("ZZGRP", "4000", "Revenue", "Up 3% on FX.")
    assert detail == {
        "consolidation_group": "ZZGRP",
        "heading": "4000",
        "heading_name": "Revenue",
        "text": "Up 3% on FX.",
    }


def test_event_detail_blank_text_is_recorded_as_empty_string():
    detail = M.event_detail("ZZGRP", "4000", "Revenue", "")
    assert detail["text"] == ""
    detail_none = M.event_detail("ZZGRP", "4000", "Revenue", None)
    assert detail_none["text"] == ""


# --- missing_commentary --------------------------------------------------------

def _headings_9():
    # lft out of insertion order on purpose, to prove lft governs the result order.
    return [
        {"heading": "1000", "heading_name": "Assets", "lft": 1},
        {"heading": "2000", "heading_name": "Liabilities", "lft": 3},
        {"heading": "3000", "heading_name": "Equity", "lft": 5},
        {"heading": "4000", "heading_name": "Revenue", "lft": 7},
        {"heading": "5000", "heading_name": "Cost of sales", "lft": 9},
        {"heading": "6000", "heading_name": "Operating expenses", "lft": 11},
        {"heading": "7000", "heading_name": "Finance costs", "lft": 13},
        {"heading": "8000", "heading_name": "Tax", "lft": 15},
        {"heading": "9000", "heading_name": "Other comprehensive income", "lft": 17},
    ]


def test_missing_commentary_over_nine_headings_two_commented_one_blank():
    headings = _headings_9()
    rows = [
        {"consolidation_group": "ZZGRP", "heading": "1000", "text": "Solid."},
        {"consolidation_group": "ZZGRP", "heading": "4000", "text": "Up 3%."},
        {"consolidation_group": "ZZGRP", "heading": "6000", "text": ""},
    ]
    result = M.missing_commentary(headings, rows, ["ZZGRP"])
    assert result == [{
        "consolidation_group": "ZZGRP",
        "headings": 9,
        "with_commentary": 2,
        "missing": [
            "Liabilities", "Equity", "Cost of sales", "Operating expenses",
            "Finance costs", "Tax", "Other comprehensive income",
        ],
    }]


def test_missing_commentary_covers_every_root_group_independently():
    headings = _headings_9()
    rows = [{"consolidation_group": "ZZGRP", "heading": "1000", "text": "Solid."}]
    result = M.missing_commentary(headings, rows, ["ZZGRP", "ZZGRP2"])
    by_group = {r["consolidation_group"]: r for r in result}
    assert by_group["ZZGRP"]["with_commentary"] == 1
    assert by_group["ZZGRP2"]["with_commentary"] == 0
    assert len(by_group["ZZGRP2"]["missing"]) == 9


# --- KEY_FIELDS ----------------------------------------------------------------

def test_key_fields_are_exactly_the_four():
    assert M.KEY_FIELDS == ("consolidation_group", "fiscal_year", "fiscal_period", "heading")


# --- module hygiene ------------------------------------------------------------

def test_module_imports_no_frappe():
    with open(MODEL_PATH) as fh:
        tree = ast.parse(fh.read())
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            assert not any(a.name.startswith(("frappe", "konsol")) for a in node.names)
        if isinstance(node, ast.ImportFrom):
            assert not (node.module or "").startswith(("frappe", "konsol"))
            assert node.level == 0


# =============================================================================
# konsol#305-W5-2 (story 8.4): commentary required above the declared
# threshold. The statements here are the REAL producer's output:
# statement_model.statement over a small chart and TB rows for P06/P07, and
# the threshold is close_policy_model.commentary_threshold's own result —
# nothing below hand-builds a statement line or a threshold.
# =============================================================================

def _load(name, filename):
    spec = importlib.util.spec_from_file_location(name, os.path.join(APP_DIR, "close", filename))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


SM = _load("statement_model_for_commentary_test", "statement_model.py")
CPM = _load("close_policy_model_for_commentary_test", "close_policy_model.py")


def _calendar():
    rows = []
    for fy in (2024, 2025):
        rows.append({"fiscal_year": fy, "fiscal_period": 0, "period_type": "Opening"})
        for fp in range(1, 13):
            rows.append({"fiscal_year": fy, "fiscal_period": fp, "period_type": "Regular"})
        rows.append({"fiscal_year": fy, "fiscal_period": 13, "period_type": "Closing"})
    return rows


_CHART = {
    "1": {"account_name": "ASSETS", "parent_account": None, "is_group": 1,
          "statement_section": SM.BS, "lft": 1, "normal_balance": "Debit"},
    "2": {"account_name": "LIABILITIES", "parent_account": None, "is_group": 1,
          "statement_section": SM.BS, "lft": 5, "normal_balance": "Credit"},
    "4": {"account_name": "REVENUE", "parent_account": None, "is_group": 1,
          "statement_section": SM.PL, "lft": 13, "normal_balance": ""},
    "6": {"account_name": "EXPENSES", "parent_account": None, "is_group": 1,
          "statement_section": SM.PL, "lft": 17, "normal_balance": ""},
    "1110": {"account_name": "Cash", "parent_account": "1", "is_group": 0,
              "statement_section": SM.BS, "lft": 2, "normal_balance": ""},
    "2100": {"account_name": "Payables", "parent_account": "2", "is_group": 0,
              "statement_section": SM.BS, "lft": 6, "normal_balance": ""},
    "4100": {"account_name": "Sales", "parent_account": "4", "is_group": 0,
              "statement_section": SM.PL, "lft": 14, "normal_balance": ""},
    "6100": {"account_name": "Opex", "parent_account": "6", "is_group": 0,
              "statement_section": SM.PL, "lft": 18, "normal_balance": ""},
}


def _tb(main_account, amount, fp):
    return {"fiscal_year": 2025, "fiscal_period": fp, "main_account": main_account,
            "adjustment_type": "entity", "amount": amount, "null_rows": 0}


#: P06 -> P07: REVENUE 10000 -> 16000 (+6000, 60%); EXPENSES -4000 -> -4100
#: (-100, 2.5%); ASSETS 6000 -> 18000 (+12000, 200%); LIABILITIES 0 -> 500
#: (+500 on a zero base: no percentage).
_ROWS = [
    _tb("4100", -10000, 6), _tb("6100", 4000, 6), _tb("1110", 6000, 6),
    _tb("4100", -16000, 7), _tb("6100", 4100, 7), _tb("1110", 12000, 7), _tb("2100", -500, 7),
]


def _statement(rows=None, key=(2025, 7)):
    return SM.statement(_ROWS if rows is None else rows, _CHART, _calendar(), key,
                        CPM.statement_accounts("", "", {}))


def _threshold(amount=0, percent=0, combine=""):
    declared = CPM.commentary_threshold(amount, percent, combine)
    assert declared["gap"] is None, declared
    return declared["threshold"]


def _required_names(result):
    return [r["heading_name"] for r in result["required"]]


def test_amount_threshold_requires_the_headings_above_it_in_statement_order():
    result = M.group_requirement(_threshold(amount=1000), _statement(), {})
    assert result["state"] == "checked"
    assert result["over_threshold"] == 2
    assert _required_names(result) == ["REVENUE", "ASSETS"]


def test_a_required_heading_carries_its_variance_and_percentage():
    result = M.group_requirement(_threshold(amount=1000), _statement(), {})
    revenue = result["required"][0]
    assert revenue == {"heading": "4", "heading_name": "REVENUE", "section": SM.PL,
                       "current": 16000.0, "comparison": 10000.0, "variance": 6000.0,
                       "percent": 60.0}


def test_percent_threshold_counts_a_zero_base_movement_as_above_it():
    result = M.group_requirement(_threshold(percent=50), _statement(), {})
    assert _required_names(result) == ["REVENUE", "ASSETS", "LIABILITIES"]
    liabilities = result["required"][2]
    assert liabilities["comparison"] == 0.0 and liabilities["percent"] is None


def test_both_values_with_both_exceeded_needs_both():
    result = M.group_requirement(
        _threshold(amount=1000, percent=50, combine="Both are exceeded"), _statement(), {})
    assert _required_names(result) == ["REVENUE", "ASSETS"]


def test_both_values_with_either_exceeded_needs_one():
    result = M.group_requirement(
        _threshold(amount=1000, percent=50, combine="Either is exceeded"), _statement(), {})
    assert _required_names(result) == ["REVENUE", "ASSETS", "LIABILITIES"]


def test_the_threshold_itself_is_not_above_it():
    # EXPENSES moved exactly 100: "above" is strictly greater.
    result = M.group_requirement(_threshold(amount=100), _statement(), {})
    assert "EXPENSES" not in _required_names(result)
    result = M.group_requirement(_threshold(amount=99.99), _statement(), {})
    assert "EXPENSES" in _required_names(result)


def test_commentary_on_a_heading_above_the_threshold_clears_it():
    result = M.group_requirement(_threshold(amount=1000), _statement(),
                                 {"4": "Price rise in July.", "1": "   "})
    assert result["over_threshold"] == 2
    assert _required_names(result) == ["ASSETS"]


def test_no_comparison_rows_is_not_comparable_with_the_statement_note():
    rows = [r for r in _ROWS if r["fiscal_period"] == 7]
    stmt = _statement(rows)
    result = M.group_requirement(_threshold(amount=1000), stmt, {})
    assert result == {"state": "not_comparable",
                      "message": stmt["periods"]["comparison_note"],
                      "over_threshold": 0, "required": []}
    assert result["message"]


def test_requirement_undeclared_reads_no_group_and_carries_the_gap_message():
    declared = CPM.commentary_threshold(0, 0, "")
    result = M.requirement(declared, None)
    assert result == {"state": "undeclared", "threshold": None,
                      "message": declared["gap"]["message"], "groups": [],
                      "required_missing": None}


def test_requirement_sums_required_headings_across_groups():
    declared = CPM.commentary_threshold(1000, 0, "")
    groups = [
        {"consolidation_group": "G1", "state": "ok", "message": None,
         "statement": _statement(), "texts": {}},
        {"consolidation_group": "G2", "state": "ok", "message": None,
         "statement": _statement(), "texts": {"4": "Explained."}},
        {"consolidation_group": "G3", "state": "no_chart",
         "message": "Publish the group chart (Main Account) first.", "statement": None,
         "texts": {}},
    ]
    result = M.requirement(declared, groups)
    assert result["state"] == "checked"
    assert result["threshold"] == declared["threshold"]
    assert result["message"] is None
    assert result["required_missing"] == 3
    assert [g["consolidation_group"] for g in result["groups"]] == ["G1", "G2", "G3"]
    assert _required_names(result["groups"][1]) == ["ASSETS"]
    assert result["groups"][2] == {"consolidation_group": "G3", "state": "checked",
                                   "message": "Publish the group chart (Main Account) first.",
                                   "over_threshold": 0, "required": []}


def test_requirement_with_an_unreadable_statement_is_unknown_naming_the_group():
    declared = CPM.commentary_threshold(1000, 0, "")
    groups = [
        {"consolidation_group": "G1", "state": "ok", "message": None,
         "statement": _statement(), "texts": {}},
        {"consolidation_group": "G2", "state": "not_built",
         "message": "ServerException (UNKNOWN_TABLE)", "statement": None, "texts": {}},
    ]
    result = M.requirement(declared, groups)
    assert result["state"] == "unknown"
    assert result["required_missing"] is None
    assert "G2" in result["message"] and "UNKNOWN_TABLE" in result["message"]
    assert result["groups"][1]["state"] == "not_built"
    assert result["groups"][1]["required"] == []


def test_requirement_refuses_an_unknown_group_state():
    declared = CPM.commentary_threshold(1000, 0, "")
    try:
        M.requirement(declared, [{"consolidation_group": "G1", "state": "bogus",
                                  "message": None, "statement": None, "texts": {}}])
    except ValueError as e:
        assert "bogus" in str(e)
    else:
        raise AssertionError("an unknown statement state was accepted")

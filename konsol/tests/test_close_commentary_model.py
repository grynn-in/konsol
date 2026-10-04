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

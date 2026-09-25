"""TB check model, pure: konsol/close/tb_model.py (konsol#305 A11, story 3.2).

check_rows() says what is wrong with each line of a parsed trial balance, with
a suggestion where one can be made, plus the file-level problems and the
totals. It is loaded by path and imports no frappe.

validate_tb_rows (the submit path) is built on it (A35, decision P1): the
identity tests prove the controller calls this very file, and that on every
fixture a file the submit path accepts is exactly a file check_rows calls ok.
"""
import ast
import importlib.util
import os
import sys
import types

_HERE = os.path.dirname(os.path.abspath(__file__))
APP_DIR = os.path.dirname(_HERE)
_MODEL = os.path.join(APP_DIR, "close", "tb_model.py")
_CONTROLLER = os.path.join(
    APP_DIR, "consolidation", "doctype",
    "trial_balance_submission", "trial_balance_submission.py",
)

_spec = importlib.util.spec_from_file_location("close_tb_model_under_test", _MODEL)
M = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(M)


# --- the controller, loaded with the stubs of test_trial_balance_submission.py:22-49

def _stub(name, **attrs):
    if name not in sys.modules:
        mod = types.ModuleType(name)
        for k, v in attrs.items():
            setattr(mod, k, v)
        sys.modules[name] = mod


class _Doc:  # stand-in for frappe.model.document.Document
    pass


_stub("frappe", whitelist=lambda *a, **k: (lambda fn: fn))
_stub("frappe.model")
_stub("frappe.model.document", Document=_Doc)
_stub("konsol", __path__=[APP_DIR])
_stub("konsol.clickhouse", execute=lambda *a, **k: "", ensure_raw_tables=lambda: None)
_stub("konsol.period_status", assert_open=lambda *a, **k: None,
      assert_postable=lambda *a, **k: None)
_stub("konsol.schema_lifecycle", check_epm_admin=lambda: None)

_cspec = importlib.util.spec_from_file_location("tbs_for_close_tb_model", _CONTROLLER)
C = importlib.util.module_from_spec(_cspec)
_cspec.loader.exec_module(C)


# --- fixtures -----------------------------------------------------------------

def _acct(is_group=0, is_posting=1):
    return {"is_group": is_group, "is_posting": is_posting}


CHART = {
    "1000": _acct(is_group=1, is_posting=0),   # heading
    "1010": _acct(),
    "2010": _acct(),
    "3010": _acct(is_posting=0),               # closed for posting
    "4010": _acct(),
}
ENTITY = "ZZA"
KNOWN = ["ZZA", "ZZB"]
BASIS = "Period movement"
TOL = 0.01


def _rows(csv_text):
    return C.parse_tb_csv(csv_text)


def _check(rows, chart=CHART, entity=ENTITY, known=KNOWN, form_basis=BASIS, tolerance=TOL):
    return M.check_rows(rows, chart, entity, known, form_basis, tolerance)


def _problems(result, line):
    (row,) = [r for r in result["rows"] if r["line"] == line]
    return row["problems"]


# --- per-row problems ------------------------------------------------------------

def test_good_file_is_ok_with_row_shape_and_totals():
    r = _check(_rows("main_account,debit,credit\n1010,100.50,0\n2010,0,100.50\n"))
    assert r["ok"] is True
    assert r["file_problems"] == []
    assert r["rows"][0] == {"line": 2, "main_account": "1010", "partner": "",
                            "debit": 100.5, "credit": 0.0, "problems": []}
    assert r["rows"][1]["line"] == 3
    assert r["totals"] == {"debit": 100.5, "credit": 100.5, "difference": 0.0}


# --- the reported line is the real CSV line, not index + 2 (konsol#305 A38) -------

def test_problem_line_survives_a_blank_line_in_the_file():
    """csv.DictReader skips blank lines, so a row problem after one must name
    the file's own line 4, not the row-count-based line 3."""
    r = _check(_rows("main_account,debit,credit\n1010,1,0\n\n4001,0,1\n"))
    assert [row["line"] for row in r["rows"]] == [2, 4]  # line 3 was blank; no row for it
    (p,) = _problems(r, 4)
    assert p["code"] == "UNKNOWN_ACCOUNT"
    assert p["message"] == "Account 4001 is not in the group chart"


def test_duplicate_message_names_the_real_line_across_a_blank_line():
    r = _check(_rows(
        "main_account,debit,credit,partner_data_area_id\n"
        "1010,10,0,ZZB\n\n1010,10,0,ZZB\n2010,0,20,\n"
    ))
    assert [row["line"] for row in r["rows"]] == [2, 4, 5]
    assert "line 4" in _problems(r, 2)[0]["message"]
    assert "line 2" in _problems(r, 4)[0]["message"]


def test_parse_tb_csvs_own_errors_name_the_real_line_after_a_blank_line():
    """Failure path: the structural errors parse_tb_csv raises itself must use
    the same physical-line counting as the rows it returns."""
    try:
        C.parse_tb_csv("main_account,debit,credit\n1010,1,0\n\n,0,1\n")
        assert False, "expected ValueError"
    except ValueError as e:
        assert "Line 4: main_account is blank" in str(e)


def test_a_row_with_no_line_key_falls_back_to_index_plus_two():
    """A caller that builds rows itself (not via parse_tb_csv) still gets a
    reasonable line number instead of a KeyError."""
    rows = [{"main_account": "1010", "debit": 10, "credit": 0},
            {"main_account": "2010", "debit": 0, "credit": 10}]
    r = _check(rows)
    assert [row["line"] for row in r["rows"]] == [2, 3]


def test_unknown_account_suggests_the_closest_posting_account():
    r = _check(_rows("main_account,debit,credit\n4001,10,0\n1010,0,10\n"))
    assert r["ok"] is False
    (p,) = _problems(r, 2)
    assert p["code"] == "UNKNOWN_ACCOUNT"
    assert p["message"] == "Account 4001 is not in the group chart"
    assert p["suggestion"] == "Did you mean 4010?"
    assert _problems(r, 3) == []


def test_unknown_account_with_nothing_close_has_no_suggestion():
    r = _check(_rows("main_account,debit,credit\nXYZ9,10,0\n1010,0,10\n"))
    (p,) = _problems(r, 2)
    assert p["code"] == "UNKNOWN_ACCOUNT"
    assert p["suggestion"] == ""


def test_heading_account_is_a_row_problem():
    r = _check(_rows("main_account,debit,credit\n1000,10,0\n1010,0,10\n"))
    (p,) = _problems(r, 2)
    assert p["code"] == "HEADING_ACCOUNT"
    assert "1000 is a heading in the group chart" in p["message"]
    assert r["ok"] is False


def test_account_closed_for_posting_is_a_row_problem():
    r = _check(_rows("main_account,debit,credit\n3010,10,0\n1010,0,10\n"))
    (p,) = _problems(r, 2)
    assert p["code"] == "CLOSED_ACCOUNT"
    assert "3010" in p["message"] and "not open for posting" in p["message"]
    assert r["ok"] is False


def test_negative_debit_is_a_row_problem_with_the_opposite_column_suggested():
    r = _check(_rows("main_account,debit,credit\n1010,-10,0\n2010,-10,0\n"))
    (p,) = _problems(r, 2)
    assert p["code"] == "NEGATIVE_AMOUNT"
    assert "opposite column" in p["message"]
    assert p["suggestion"] == "Enter 10.00 as a credit instead"
    assert r["ok"] is False


def test_duplicate_pair_flags_every_line_of_the_pair():
    r = _check(_rows(
        "main_account,debit,credit,partner_data_area_id\n"
        "1010,10,0,ZZB\n2010,0,20,\n1010,10,0,ZZB\n"
    ))
    assert [p["code"] for p in _problems(r, 2)] == ["DUPLICATE_ROW"]
    assert [p["code"] for p in _problems(r, 4)] == ["DUPLICATE_ROW"]
    assert _problems(r, 3) == []
    assert "line 4" in _problems(r, 2)[0]["message"]
    assert "line 2" in _problems(r, 4)[0]["message"]


def test_same_account_with_different_partners_is_not_a_duplicate():
    r = _check(_rows(
        "main_account,debit,credit,partner_data_area_id\n"
        "1010,10,0,ZZB\n1010,10,0,\n2010,0,20,\n"
    ))
    assert r["ok"] is True


def test_partner_equal_to_the_entity_is_a_row_problem():
    r = _check(_rows(
        "main_account,debit,credit,partner_data_area_id\n1010,10,0,zza\n2010,0,10,\n"
    ))
    (p,) = _problems(r, 2)
    assert p["code"] == "SELF_PARTNER"
    assert "ZZA" in p["message"]
    assert r["ok"] is False


def test_unknown_partner_with_a_case_only_match_suggests_it():
    r = _check(_rows(
        "main_account,debit,credit,partner_data_area_id\n1010,10,0,zzb\n2010,0,10,\n"
    ))
    (p,) = _problems(r, 2)
    assert p["code"] == "UNKNOWN_PARTNER"
    assert "zzb" in p["message"]
    assert p["suggestion"] == "Did you mean ZZB?"


def test_unknown_partner_with_no_match_has_no_suggestion():
    r = _check(_rows(
        "main_account,debit,credit,partner_data_area_id\n1010,10,0,QQQ\n2010,0,10,\n"
    ))
    (p,) = _problems(r, 2)
    assert p["code"] == "UNKNOWN_PARTNER"
    assert p["suggestion"] == ""


def test_partner_checks_are_skipped_when_not_given():
    rows = _rows("main_account,debit,credit,partner_data_area_id\n1010,10,0,QQQ\n2010,0,10,\n")
    assert M.check_rows(rows, CHART, None, None, BASIS, TOL)["ok"] is True


def test_basis_cell_contradicting_the_form_is_a_row_problem():
    r = _check(_rows(
        "main_account,debit,credit,amount_basis\n"
        "1010,10,0,Period movement\n2010,0,10,Period-end balance\n"
    ))
    assert _problems(r, 2) == []
    (p,) = _problems(r, 3)
    assert p["code"] == "AMOUNT_BASIS"
    assert "Period-end balance" in p["message"] and "Line 3" in p["message"]
    assert r["ok"] is False


def test_missing_form_basis_is_a_file_problem_not_repeated_per_row():
    r = _check(_rows("main_account,debit,credit,amount_basis\n1010,10,0,Period movement\n2010,0,10,\n"),
               form_basis="")
    assert any(p.startswith("Amount Basis is required") for p in r["file_problems"])
    assert _problems(r, 2) == [] and _problems(r, 3) == []
    assert r["ok"] is False


# --- file-level problems -----------------------------------------------------------

def test_imbalance_is_a_file_problem_with_totals():
    r = _check(_rows("main_account,debit,credit\n1010,100,0\n2010,0,90\n"))
    assert r["totals"] == {"debit": 100.0, "credit": 90.0, "difference": 10.0}
    assert len(r["file_problems"]) == 1
    assert "Debits (100.00) do not equal credits (90.00)" in r["file_problems"][0]
    assert all(row["problems"] == [] for row in r["rows"])
    assert r["ok"] is False


def test_imbalance_within_tolerance_is_ok():
    # 100.01 - 100 is 0.010000000000005 in raw floats, just over 0.01 (A37):
    # the comparison must round to cents first, since parse_tb_csv already
    # rounds every amount to cents. Default tolerance (0.01), no dodge.
    rows = _rows("main_account,debit,credit\n1010,100.01,0\n2010,0,100\n")
    r = _check(rows)
    assert r["ok"] is True
    assert r["totals"]["difference"] == 0.01
    assert C.validate_tb_rows(rows, chart=CHART, entity=ENTITY, known_entities=KNOWN) == []


def test_imbalance_just_over_tolerance_is_still_refused():
    """Failure path: 0.02 is not the tolerance; it must still be refused."""
    r = _check(_rows("main_account,debit,credit\n1010,100.02,0\n2010,0,100\n"))
    assert r["ok"] is False
    assert r["totals"]["difference"] == 0.02


def test_no_chart_is_the_no_chart_file_problem():
    """Failure path: no published chart is one refusal, not every account listed."""
    for chart in (None, {}):
        r = _check(_rows("main_account,debit,credit\n1010,10,0\n2010,0,10\n"), chart=chart)
        assert r["file_problems"] == [C.NO_CHART]
        assert all(row["problems"] == [] for row in r["rows"])
        assert r["ok"] is False


def test_no_chart_text_is_the_controllers():
    assert M.NO_CHART == C.NO_CHART


def test_tolerance_constant_matches_the_controller():
    assert M.BALANCE_TOLERANCE == C.BALANCE_TOLERANCE


# --- identity with validate_tb_rows (Problems 1, decision P1, A35) -------------------

PARITY = {
    "good": "main_account,debit,credit\n1010,100,0\n2010,0,100\n",
    "unknown account": "main_account,debit,credit\n4001,100,0\n2010,0,100\n",
    "heading": "main_account,debit,credit\n1000,100,0\n2010,0,100\n",
    "closed": "main_account,debit,credit\n3010,100,0\n2010,0,100\n",
    "negative": "main_account,debit,credit\n1010,-100,0\n2010,-100,0\n",
    "duplicate": "main_account,debit,credit\n1010,50,0\n1010,50,0\n2010,0,100\n",
    "self partner": ("main_account,debit,credit,partner_data_area_id\n"
                     "1010,100,0,ZZA\n2010,0,100,\n"),
    "imbalance": "main_account,debit,credit\n1010,100,0\n2010,0,50\n",
}


def test_the_controller_uses_this_check_rows():
    """One rule set: the submit path's check_rows is this module's, not a copy."""
    assert hasattr(C, "check_rows"), "validate_tb_rows must be built on tb_model.check_rows (A35)"
    assert os.path.samefile(C.check_rows.__code__.co_filename, _MODEL)
    assert C.check_rows.__code__.co_code == M.check_rows.__code__.co_code


def test_identity_with_validate_tb_rows_on_every_fixture():
    for name, text in PARITY.items():
        rows = _rows(text)
        submit_ok = C.validate_tb_rows(rows, chart=CHART, entity=ENTITY, known_entities=KNOWN) == []
        check_ok = _check(rows)["ok"]
        assert submit_ok == check_ok, f"{name}: validate_tb_rows ok={submit_ok}, check_rows ok={check_ok}"
    # The fixtures are not all trivially one way.
    assert _check(_rows(PARITY["good"]))["ok"] is True
    assert sum(1 for t in PARITY.values() if not _check(_rows(t))["ok"]) == 7


def test_identity_with_no_chart():
    rows = _rows(PARITY["good"])
    assert (C.validate_tb_rows(rows, chart={}, entity=ENTITY, known_entities=KNOWN) == []) \
        == _check(rows, chart={})["ok"]


def test_module_imports_no_frappe():
    with open(_MODEL) as fh:
        tree = ast.parse(fh.read())
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            assert not any(a.name.startswith("frappe") for a in node.names)
        if isinstance(node, ast.ImportFrom):
            assert not (node.module or "").startswith("frappe")

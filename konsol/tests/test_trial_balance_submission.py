"""Host tests for the Trial Balance Submission CSV parser and validator.

parse_tb_csv / validate_tb_rows are deliberately pure so the whole validation
surface runs here without a site. The module is loaded by file path with
stubbed frappe/konsol imports (the pattern test_budget_grain.py established) —
the pure functions under test never call into them. A naive AST-extraction
loader was tried first and silently dropped this whole file from the suite the
day a new import was added; module stubbing fails loudly instead.
"""
import importlib.util
import os
import sys
import types

_HERE = os.path.dirname(os.path.abspath(__file__))
_SRC = os.path.join(
    _HERE, "..", "consolidation", "doctype",
    "trial_balance_submission", "trial_balance_submission.py",
)


def _stub(name, **attrs):
    if name not in sys.modules:
        mod = types.ModuleType(name)
        for k, v in attrs.items():
            setattr(mod, k, v)
        sys.modules[name] = mod


class _Doc:  # stand-in for frappe.model.document.Document
    pass


_stub("frappe")
_stub("frappe.model")
_stub("frappe.model.document", Document=_Doc)
_stub("konsol")
_stub("konsol.clickhouse", execute=lambda *a, **k: "")
_stub("konsol.period_status", assert_open=lambda *a, **k: None)

_spec = importlib.util.spec_from_file_location("tbs_under_test", _SRC)
_m = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_m)

GOOD = "main_account,debit,credit\n1010,100.50,0\n2010,0,100.50\n"


def test_parse_good_file():
    rows = _m.parse_tb_csv(GOOD)
    assert len(rows) == 2
    assert rows[0] == {"main_account": "1010", "debit": 100.5,
                       "credit": 0.0, "description": ""}


def test_parse_accepts_description_and_case_insensitive_header():
    rows = _m.parse_tb_csv(
        "Main_Account,DEBIT,Credit,Description\n1010,5,0,Cash\n2010,0,5,AP\n")
    assert rows[0]["description"] == "Cash"


def test_parse_rejects_missing_columns():
    try:
        _m.parse_tb_csv("account,dr,cr\n1010,1,0\n")
        assert False, "expected ValueError"
    except ValueError as e:
        assert "main_account" in str(e)


def test_parse_rejects_non_numeric_amount():
    try:
        _m.parse_tb_csv("main_account,debit,credit\n1010,abc,0\n")
        assert False, "expected ValueError"
    except ValueError as e:
        assert "Line 2" in str(e)


def test_parse_rejects_blank_account_and_empty_file():
    for bad in ("main_account,debit,credit\n,1,0\n",
                "",
                "main_account,debit,credit\n"):
        try:
            _m.parse_tb_csv(bad)
            assert False, f"expected ValueError for {bad!r}"
        except ValueError:
            pass


def test_parse_treats_blank_amounts_as_zero():
    rows = _m.parse_tb_csv("main_account,debit,credit\n1010,,\n2010,0,0\n")
    assert rows[0]["debit"] == 0.0 and rows[0]["credit"] == 0.0


def _rows(*triples):
    return [{"main_account": a, "debit": d, "credit": c, "description": ""}
            for a, d, c in triples]


def test_validate_balanced_clean():
    assert _m.validate_tb_rows(_rows(("1010", 10, 0), ("2010", 0, 10))) == []


def test_validate_flags_imbalance():
    errs = _m.validate_tb_rows(_rows(("1010", 10, 0), ("2010", 0, 9)))
    assert any("do not equal" in e for e in errs)


def test_validate_tolerates_rounding_within_tolerance():
    assert _m.validate_tb_rows(
        _rows(("1010", 10.004, 0), ("2010", 0, 10.0))) == []


def test_validate_flags_duplicates():
    errs = _m.validate_tb_rows(
        _rows(("1010", 5, 0), ("1010", 5, 0), ("2010", 0, 10)))
    assert any("Duplicate" in e and "1010" in e for e in errs)


def test_validate_flags_negative_amounts():
    errs = _m.validate_tb_rows(_rows(("1010", -10, 0), ("2010", 0, -10)))
    assert any("Negative" in e for e in errs)


def test_validate_flags_unknown_accounts_only_when_chart_given():
    rows = _rows(("1010", 10, 0), ("9999", 0, 10))
    assert _m.validate_tb_rows(rows) == []  # no chart -> skipped
    errs = _m.validate_tb_rows(rows, known_accounts={"1010", "2010"})
    assert any("9999" in e for e in errs)
    assert not any("1010" in e for e in errs if "chart" in e)


def test_validate_collects_multiple_errors():
    errs = _m.validate_tb_rows(
        _rows(("1010", 5, 0), ("1010", -1, 0)), known_accounts={"2010"})
    assert len(errs) >= 3  # duplicate + negative + unknown (+ imbalance)


def test_sql_str_escapes_quotes_and_backslashes():
    assert _m._sql_str("O'Brien\\x") == "O\\'Brien\\\\x"


def test_parse_rejects_nan_and_inf():
    for bad in ("nan", "inf", "-inf"):
        try:
            _m.parse_tb_csv(f"main_account,debit,credit\n1010,{bad},0\n")
            assert False, f"expected ValueError for {bad}"
        except ValueError as e:
            assert "finite" in str(e)


def test_parse_rejects_surplus_cells():
    try:
        _m.parse_tb_csv("main_account,debit,credit\n1010,1,0,stray,extra\n")
        assert False, "expected ValueError"
    except ValueError as e:
        assert "more cells" in str(e)


def test_parse_rounds_to_cents_so_stored_equals_validated():
    rows = _m.parse_tb_csv("main_account,debit,credit\n1010,10.005,0\n2010,0,10.004\n")
    assert rows[0]["debit"] == 10.0 or rows[0]["debit"] == 10.01  # banker's rounding either way
    assert rows[1]["credit"] == 10.0
    # the point: balance is judged on the ROUNDED values — the same numbers
    # the warehouse will store — so post-rounding drift past the tolerance
    # fails here, not later in a dbt test
    errs = _m.validate_tb_rows(_m.parse_tb_csv(
        "main_account,debit,credit\n1010,10.019,0\n2010,0,10.001\n"))
    assert any("do not equal" in e for e in errs)  # 10.02 vs 10.00 -> 0.02 > 0.01

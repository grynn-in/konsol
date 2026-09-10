"""Host tests for the Trial Balance Submission CSV parser and validator.

parse_tb_csv / validate_tb_rows are deliberately pure so the whole validation
surface runs here without a site. The functions are imported by file path
because importing the module would pull in frappe.
"""
import importlib.util
import os

_HERE = os.path.dirname(os.path.abspath(__file__))
_SRC = os.path.join(
    _HERE, "..", "consolidation", "doctype",
    "trial_balance_submission", "trial_balance_submission.py",
)


def _load_pure_functions():
    """Extract the pure helpers without importing frappe."""
    import ast
    import types
    with open(_SRC) as f:
        tree = ast.parse(f.read())
    wanted = {"parse_tb_csv", "validate_tb_rows", "_sql_str",
              "_REQUIRED_COLUMNS", "BALANCE_TOLERANCE"}
    module = ast.Module(
        body=[n for n in tree.body
              if (isinstance(n, (ast.FunctionDef, ast.Assign))
                  and (getattr(n, "name", None) in wanted
                       or any(getattr(t, "id", None) in wanted
                              for t in getattr(n, "targets", []))))
              or (isinstance(n, (ast.Import, ast.ImportFrom))
                  and "frappe" not in ast.dump(n)
                  and "clickhouse" not in ast.dump(n))],
        type_ignores=[],
    )
    ns = types.ModuleType("tbs_pure")
    exec(compile(module, _SRC, "exec"), ns.__dict__)
    return ns


_m = _load_pure_functions()

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

"""konsol#353 option B: a Consolidation Journal is exact in its currency's ISO minor unit.

Chosen under Deepak Pai's standing rule of 5 Nov 2026 (product, configurable,
not opinionated); the HMD table is on the issue. The journal's currency is the
root group's reporting currency (set in validate()). Its precision is
``ISO Currency.minor_unit`` (konsol#330 ties it to the ISO 4217 list):

- a line amount with more decimal places than the minor unit is refused, so
  the balance check is exact in the currency's unit (a BHD journal off by
  0.001 was accepted, because amounts were rounded to the cent);
- a currency with no minor unit is refused, never read as 2;
- a minor unit above the warehouse column's scale (``STORED_PLACES``) is
  refused, naming the scale, until the column is widened (option C, which
  needs Deepak). The TB rule does the same (tb_balance_model.place_problems).

The rule is ``journal_model.currency_problems`` (pure, loaded by path).
"""
import ast
import importlib.util
import os
import re

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PATH = os.path.join(APP_DIR, "close", "journal_model.py")
_spec = importlib.util.spec_from_file_location("journal_model_353", _PATH)
M = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(M)
CONTROLLER = os.path.join(APP_DIR, "consolidation", "doctype", "consolidation_journal",
                          "consolidation_journal.py")


def _line(idx, debit=0, credit=0):
    return {"idx": idx, "main_account": "1000", "debit_amount": debit, "credit_amount": credit}


def test_a_clean_usd_journal_has_no_currency_problem():
    lines = [_line(1, debit=100.25), _line(2, credit=100.25)]
    assert M.currency_problems("USD", 2, lines) == []


def test_more_places_than_the_minor_unit_is_refused_naming_the_line():
    """The defect: 100.004 vs 100.001 both rounded to 100.00 and balanced."""
    lines = [_line(1, debit=100.004), _line(2, credit=100.001)]
    problems = M.currency_problems("USD", 2, lines)
    assert len(problems) == 2, problems
    assert "Line 1" in problems[0] and "100.004" in problems[0] and "USD has 2" in problems[0]


def test_a_zero_decimal_currency_refuses_fractions():
    problems = M.currency_problems("JPY", 0, [_line(1, debit=1000.5), _line(2, credit=1000.5)])
    assert len(problems) == 2 and "JPY has 0" in problems[0], problems
    assert M.currency_problems("JPY", 0, [_line(1, debit=1000), _line(2, credit=1000.0)]) == []


def test_an_undeclared_minor_unit_is_refused_never_read_as_two():
    for minor_unit in (None, "", "2", True, -1):
        problems = M.currency_problems("ZZX", minor_unit, [_line(1, debit=1), _line(2, credit=1)])
        assert len(problems) == 1 and "ZZX" in problems[0] and "ISO Currency" in problems[0], minor_unit


def test_a_journal_with_no_currency_is_refused():
    problems = M.currency_problems(None, None, [_line(1, debit=1), _line(2, credit=1)])
    assert len(problems) == 1 and "currency" in problems[0].lower()


def test_a_currency_finer_than_the_warehouse_column_is_refused_naming_the_scale():
    """BHD (3) cannot be stored in Decimal(18,2) without losing its third
    decimal: refused until the column is widened (option C)."""
    problems = M.currency_problems("BHD", 3, [_line(1, debit=1.5), _line(2, credit=1.5)])
    assert len(problems) == 1, problems
    assert "BHD" in problems[0] and "3" in problems[0] and str(M.STORED_PLACES) in problems[0]


def test_stored_places_is_the_warehouse_columns_scale():
    """Not a rule kept by hand: it must equal the scale in konsol's DDL."""
    with open(os.path.join(APP_DIR, "clickhouse.py")) as f:
        src = f.read()
    ddl = src.split('"epm_staging.consolidation_adjustments": (', 1)[1].split("),", 1)[0]
    scales = {int(s) for s in re.findall(r"(?:debit|credit)_amount Decimal\(\d+,\s*(\d+)\)", ddl)}
    assert scales == {M.STORED_PLACES}, scales


def test_the_controller_runs_the_currency_rule_with_the_iso_minor_unit():
    with open(CONTROLLER) as f:
        tree = ast.parse(f.read())
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef))
    fn = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == "_validate_lines_and_totals")
    src = ast.unparse(fn)
    assert "journal_model.currency_problems" in src, src
    assert "'ISO Currency'" in src and "'minor_unit'" in src, src
    # the currency is known before the lines are judged
    assert src.index("reporting_currency") < src.index("currency_problems"), src

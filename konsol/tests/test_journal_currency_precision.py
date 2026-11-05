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


import json

with open(os.path.join(APP_DIR, "reference_data", "iso_currencies.json")) as _f:
    #: The shipped ISO 4217 list as {code: minor_unit} (None: ISO sets none).
    L = {r["currency_code"]: r["minor_unit"] for r in json.load(_f)}


def _line(idx, debit=0, credit=0):
    return {"idx": idx, "main_account": "1000", "debit_amount": debit, "credit_amount": credit}


def test_a_clean_usd_journal_has_no_currency_problem():
    lines = [_line(1, debit=100.25), _line(2, credit=100.25)]
    assert M.currency_problems("USD", 2, lines, L) == []


def test_more_places_than_the_minor_unit_is_refused_naming_the_line():
    """The defect: 100.004 vs 100.001 both rounded to 100.00 and balanced."""
    lines = [_line(1, debit=100.004), _line(2, credit=100.001)]
    problems = M.currency_problems("USD", 2, lines, L)
    assert len(problems) == 2, problems
    assert "Line 1" in problems[0] and "100.004" in problems[0] and "USD has 2" in problems[0]


def test_a_zero_decimal_currency_refuses_fractions():
    problems = M.currency_problems("JPY", 0, [_line(1, debit=1000.5), _line(2, credit=1000.5)], L)
    assert len(problems) == 2 and "JPY has 0" in problems[0], problems
    assert M.currency_problems("JPY", 0, [_line(1, debit=1000), _line(2, credit=1000.0)], L) == []


def test_an_undeclared_minor_unit_is_refused_never_read_as_two():
    for minor_unit in (None, "", "2", True, -1):
        problems = M.currency_problems("ZZX", minor_unit, [_line(1, debit=1), _line(2, credit=1)], L)
        assert len(problems) == 1 and "ZZX" in problems[0] and "ISO Currency" in problems[0], minor_unit


def test_a_journal_with_no_currency_is_refused():
    problems = M.currency_problems(None, None, [_line(1, debit=1), _line(2, credit=1)], L)
    assert len(problems) == 1 and "currency" in problems[0].lower()


def test_a_currency_finer_than_the_warehouse_column_is_refused_naming_the_scale():
    """A declared 5-place unit cannot be stored in Decimal(20,4) without
    losing its fifth decimal: refused, naming the scale (option B's guard,
    which option C keeps above 4 places)."""
    problems = M.currency_problems("ZZFIVE", 5, [_line(1, debit=1.5), _line(2, credit=1.5)], L,
                                   declared=True)
    assert len(problems) == 1, problems
    assert "ZZFIVE" in problems[0] and "5" in problems[0] and str(M.STORED_PLACES) in problems[0]


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


# --- PR #387 review ----------------------------------------------------------


def test_a_stored_minor_unit_that_disagrees_with_iso_is_refused_naming_330():
    """Finding 1: USD stored as 0 (the konsol#330 defect) gave "USD has 0",
    which blames the amounts. The listed ISO unit is the judge; a stored
    value that disagrees is refused as what it is."""
    lines = [_line(1, debit=100.25), _line(2, credit=100.25)]
    problems = M.currency_problems("USD", 0, lines, L)
    assert len(problems) == 1, problems
    assert "#330" in problems[0] and "0" in problems[0] and "2" in problems[0], problems
    assert "USD has 0" not in problems[0], problems
    assert M.currency_problems("USD", 2, lines, L) == []


def test_a_code_iso_sets_no_unit_for_is_judged_by_its_stored_unit():
    """Off the list or N.A. (declared under konsol#330): the stored value."""
    lines = [_line(1, debit=100), _line(2, credit=100)]
    assert M.currency_problems("ZZPTS", 0, lines, L, declared=True) == []
    problems = M.currency_problems("ZZPTS", 0, [_line(1, debit=1.5), _line(2, credit=1.5)], L,
                                   declared=True)
    assert len(problems) == 2 and "ZZPTS has 0" in problems[0], problems


def test_places_are_counted_on_the_value_as_frappe_stores_it():
    """Finding 2: str(0.1 + 0.2) is 0.30000000000000004, 17 places. Frappe
    stores a Currency to 9 places, so it is 0.3."""
    total = 0.1 + 0.2
    assert M._decimal_places(total) == 1
    assert M._decimal_places("0.30000000000000004") == 1
    assert M._decimal_places(100.004) == 3
    assert M._decimal_places(1e-10) == 0
    assert M._decimal_places(None) == 0
    lines = [_line(1, debit=total), _line(2, credit=0.3)]
    assert M.currency_problems("USD", 2, lines, L) == []


def test_the_controller_passes_the_listed_iso_unit():
    with open(CONTROLLER) as f:
        tree = ast.parse(f.read())
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef))
    fn = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == "_validate_lines_and_totals")
    src = ast.unparse(fn)
    assert "listed_minor_units" in src and "minor_unit_declared" in src, src



# --- PR #387 re-review: #330's own rule, and amounts too large to judge ------


def test_an_unticked_xdr_zero_is_refused_by_the_330_rule():
    """The hand-written comparison read XDR's listed None as "no list unit"
    and accepted the stored 0. The #330 rule refuses it: declare it."""
    lines = [_line(1, debit=100), _line(2, credit=100)]
    problems = M.currency_problems("XDR", 0, lines, L)
    assert len(problems) == 1, problems
    assert "XDR" in problems[0] and "Minor Unit Not Set by ISO 4217" in problems[0], problems
    assert M.currency_problems("XDR", 0, lines, L, declared=True) == []


def test_the_rule_is_iso_currency_model_not_a_copy():
    with open(_PATH) as f:
        tree = ast.parse(f.read())
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "currency_problems")
    src = ast.unparse(fn)
    assert "minor_unit_problem(" in src, src
    assert "listed_unit" not in src, src


def test_an_amount_of_1e19_or_more_is_refused_with_a_sentence_not_an_exception():
    for amount in (1e19, "1e19", 10**19, 1e30, "1e400", -1e19):
        lines = [_line(1, debit=amount), _line(2, credit=1)]
        problems = M.line_problems(lines)
        assert any("Line 1" in p and "too large" in p for p in problems), (amount, problems)
        # currency_problems leaves it to line_problems and does not raise
        M.currency_problems("USD", 2, lines, L)


# --- PR #387 final: the limit is what decimal(21,9) stores (12 whole digits) --


def test_the_limit_is_12_whole_digits_the_decimal_21_9_column_stores():
    """Journal amounts and totals are Frappe Currency fields, stored as
    MariaDB decimal(21,9): 12 whole digits, so 10^12 does not fit."""
    from decimal import Decimal
    assert M.MAX_AMOUNT == Decimal(10) ** 12 and isinstance(M.MAX_AMOUNT, Decimal)
    for amount in (10**12, 1e12, "1000000000000", 5e15, -1e12):
        problems = M.line_problems([_line(1, debit=amount), _line(2, credit=1)])
        assert any("Line 1" in p and "too large" in p and "12" in p for p in problems), (amount, problems)
        assert not any("10^19" in p for p in problems), problems
    ok = [_line(1, debit="999999999999.99"), _line(2, credit="999999999999.99")]
    assert M.line_problems(ok) == []
    # just below the limit, places are still counted
    assert M.currency_problems("USD", 2, [_line(1, debit="999999999999.004"), _line(2, credit=1)], L)


def test_totals_that_sum_past_the_limit_are_refused():
    """Each line fits, but the total_debit / total_credit columns are
    decimal(21,9) too: two lines of 6e11 sum to 1.2e12."""
    assert M.total_problems(6e11 * 2, 6e11 * 2) == [
        "The total debit 1200000000000.0 is too large; a journal's totals must be less than "
        "1,000,000,000,000 (10^12): a Currency field stores 12 whole digits.",
        "The total credit 1200000000000.0 is too large; a journal's totals must be less than "
        "1,000,000,000,000 (10^12): a Currency field stores 12 whole digits.",
    ]
    assert M.total_problems(999999999999.99, 999999999999.99) == []


# --- konsol#353 option C (Deepak Pai, 5 Nov 2026): the warehouse keeps 4 places


def test_the_warehouse_keeps_four_decimal_places():
    assert M.STORED_PLACES == 4


def test_a_three_decimal_journal_is_accepted_and_balances_exactly():
    """BHD, KWD and OMR (3) and CLF (4) can post journals now."""
    lines = [_line(i, debit=0.004) for i in range(1, 6)] + [_line(6, credit="0.020")]
    for code, unit in (("BHD", 3), ("KWD", 3), ("OMR", 3), ("CLF", 4)):
        assert M.currency_problems(code, unit, lines, L) == [], code
    assert M.line_problems(lines) == []
    total_debit, total_credit = M.totals(lines)
    assert (float(total_debit), float(total_credit)) == (0.02, 0.02)
    assert M.balance_problem(total_debit, total_credit) is None


def test_a_three_decimal_imbalance_is_refused_not_rounded_away():
    """0.016 against 0.004 was 0.02 against 0.00 in cents; 0.0101 against
    0.0100 (CLF) balanced at the cent. Judged at the stored scale now."""
    total_debit, total_credit = M.totals([_line(1, debit=0.016), _line(2, credit=0.004)])
    assert (float(total_debit), float(total_credit)) == (0.016, 0.004)
    assert "0.016" in M.balance_problem(total_debit, total_credit)
    total_debit, total_credit = M.totals([_line(1, debit="0.0101"), _line(2, credit="0.0100")])
    assert M.balance_problem(total_debit, total_credit) is not None


def test_a_sub_cent_line_is_an_amount_not_a_blank_line():
    """A BHD line of 0.004 is a debit: line_problems used to read it as 0.00
    and say "enter a debit or a credit"."""
    assert M.line_problems([_line(1, debit=0.004), _line(2, credit=0.004)]) == []



# --- PR #394 review: totals and the balance compared as Decimal -------------


def test_a_tenth_of_a_basis_point_imbalance_on_a_large_journal_is_refused():
    """Above ~5.5e11 a float cannot hold 0.0001: two totals that differ by it
    compared equal as floats. The controller compares Decimals."""
    from decimal import Decimal
    lines = [_line(1, debit="999999999999.9999"), _line(2, credit="999999999999.9998")]
    total_debit, total_credit = M.totals(lines)
    assert isinstance(total_debit, Decimal) and isinstance(total_credit, Decimal)
    assert total_debit - total_credit == Decimal("0.0001")
    assert M.balance_problem(total_debit, total_credit) is not None


def test_the_controller_stores_the_totals_as_floats_after_judging_decimals():
    with open(CONTROLLER) as f:
        src = f.read()
    assert "float(total_debit)" in src and "float(total_credit)" in src, src

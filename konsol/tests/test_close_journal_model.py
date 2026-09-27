"""Consolidation Journal rules, pure: konsol/close/journal_model.py
(konsol#305 J01; #292 "What validate() gains").

Loaded by path; the module imports nothing from frappe or konsol.
"""
import ast
import importlib.util
import os

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PATH = os.path.join(APP_DIR, "close", "journal_model.py")
_spec = importlib.util.spec_from_file_location("close_journal_model_under_test", _PATH)
M = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(M)


def _line(idx, debit=0, credit=0):
    return {"idx": idx, "main_account": "1000", "debit_amount": debit, "credit_amount": credit}


def test_a_line_with_both_amounts_is_refused_naming_the_line():
    lines = [_line(1, debit=100), _line(2, debit=50, credit=50)]
    problems = M.line_problems(lines)
    assert problems
    assert any("2" in p for p in problems), problems


def test_a_line_with_neither_amount_is_refused():
    lines = [_line(1, debit=100), _line(2)]
    problems = M.line_problems(lines)
    assert problems
    assert any("2" in p for p in problems), problems


def test_a_negative_amount_is_refused():
    lines = [_line(1, debit=100), _line(2, debit=-50)]
    problems = M.line_problems(lines)
    assert problems
    assert any("2" in p for p in problems), problems


def test_a_single_line_is_refused():
    lines = [_line(1, debit=100)]
    problems = M.line_problems(lines)
    assert problems


def test_a_clean_two_line_journal_has_no_line_problems():
    lines = [_line(1, debit=100), _line(2, credit=100)]
    assert M.line_problems(lines) == []


def test_totals_and_balance_problem_name_both_totals():
    lines = [_line(1, debit=100.00), _line(2, credit=99.99)]
    total_debit, total_credit = M.totals(lines)
    problem = M.balance_problem(total_debit, total_credit)
    assert problem is not None
    assert "100" in problem
    assert "99.99" in problem


def test_fractional_cents_balance_exactly():
    """0.1 + 0.2 must balance against 0.3 — a plain float sum would not."""
    lines = [_line(1, debit=0.10), _line(2, debit=0.20), _line(3, credit=0.30)]
    total_debit, total_credit = M.totals(lines)
    assert M.balance_problem(total_debit, total_credit) is None


def test_a_clean_journal_has_no_problems_at_all():
    lines = [_line(1, debit=100), _line(2, credit=100)]
    problems = list(M.line_problems(lines))
    total_debit, total_credit = M.totals(lines)
    bp = M.balance_problem(total_debit, total_credit)
    if bp:
        problems.append(bp)
    assert problems == []


def _period_row(fiscal_year, fiscal_period, period_type="Regular", status="Open"):
    """A fiscal_calendar.fiscal_period_rows() row, minimal but with every key
    reversal_problem might read."""
    return {
        "fiscal_year": fiscal_year,
        "fiscal_period": fiscal_period,
        "period_code": f"P{fiscal_period}",
        "period_label": f"P{fiscal_period}",
        "period_type": period_type,
        "start_date": None,
        "end_date": None,
        "quarter": "",
        "status": status,
    }


#: The journal's own period is FY2024 P12.
_OWN = (2024, 12)
_ROWS = [
    _period_row(2024, 11, "Regular", "Open"),      # an earlier period
    _period_row(2024, 12, "Regular", "Open"),       # the journal's own period
    _period_row(2024, 13, "Closing", "Open"),       # a Closing period
    _period_row(2025, 1, "Regular", "Open"),        # next year's P1: Regular + Open
    _period_row(2025, 2, "Regular", "Closed"),      # a Closed target
    _period_row(2025, 3, "Regular", "Locked"),       # a Locked target
]


def test_reversal_pair_problem_both_zero_is_none():
    assert M.reversal_pair_problem(0, 0) is None


def test_reversal_pair_problem_year_only_is_refused():
    assert M.reversal_pair_problem(2025, 0) is not None


def test_reversal_pair_problem_period_only_is_refused():
    assert M.reversal_pair_problem(0, 1) is not None


def test_reversal_problem_both_zero_is_none():
    assert M.reversal_problem(*_OWN, 0, 0, _ROWS) is None


def test_reversal_problem_an_undeclared_period_is_refused():
    problem = M.reversal_problem(*_OWN, 2099, 1, _ROWS)
    assert problem is not None and "declared" in problem


def test_reversal_problem_a_closing_period_is_refused():
    problem = M.reversal_problem(*_OWN, 2024, 13, _ROWS)
    assert problem is not None and "Closing" in problem


def test_reversal_problem_the_journals_own_period_is_refused():
    problem = M.reversal_problem(*_OWN, *_OWN, _ROWS)
    assert problem is not None and "after" in problem


def test_reversal_problem_an_earlier_period_is_refused():
    problem = M.reversal_problem(*_OWN, 2024, 11, _ROWS)
    assert problem is not None and "after" in problem


def test_reversal_problem_a_closed_target_is_refused():
    problem = M.reversal_problem(*_OWN, 2025, 2, _ROWS)
    assert problem is not None and "Closed" in problem


def test_reversal_problem_a_locked_target_is_refused():
    problem = M.reversal_problem(*_OWN, 2025, 3, _ROWS)
    assert problem is not None and "Locked" in problem


def test_reversal_problem_next_years_open_regular_period_is_none():
    assert M.reversal_problem(*_OWN, 2025, 1, _ROWS) is None


def test_module_imports_no_frappe():
    with open(_PATH) as fh:
        tree = ast.parse(fh.read())
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            assert not any(a.name.split(".")[0] in ("frappe", "konsol") for a in node.names)
        if isinstance(node, ast.ImportFrom):
            assert (node.module or "").split(".")[0] not in ("frappe", "konsol")

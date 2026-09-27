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


def test_module_imports_no_frappe():
    with open(_PATH) as fh:
        tree = ast.parse(fh.read())
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            assert not any(a.name.split(".")[0] in ("frappe", "konsol") for a in node.names)
        if isinstance(node, ast.ImportFrom):
            assert (node.module or "").split(".")[0] not in ("frappe", "konsol")

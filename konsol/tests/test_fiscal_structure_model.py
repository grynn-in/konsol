"""Fiscal year period-row rules (konsol#189), pure: konsol/fiscal_structure_model.py.

Loaded by path; the module imports nothing from frappe or konsol.
"""
import importlib.util
import os

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_spec = importlib.util.spec_from_file_location(
    "fsm_under_test", os.path.join(APP_DIR, "fiscal_structure_model.py"))
M = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(M)


def row(period, code):
    return {"period": period, "code": code}


def test_duplicate_number_and_code_named():
    rows = [row(1, "P01"), row(3, "P03"), row(4, "P04"), row(5, "P05"), row(3, "P03")]
    errors = M.period_problems(rows)
    assert any("Period 3 is used by rows 2 and 5" in e for e in errors), errors
    assert any("Code 'P03' is used by rows 2 and 5" in e for e in errors), errors
    # case-insensitive code match
    assert any("used by rows" in e for e in M.period_problems([row(1, "p01"), row(2, "P01")]))


def test_period_number_range():
    assert M.period_problems([row(-1, "P01")])
    assert any("Row 1: period -1 is outside 0..255" in e for e in M.period_problems([row(-1, "P01")]))
    assert M.period_problems([row(256, "P01")])
    assert any("Row 1: period 256 is outside 0..255" in e for e in M.period_problems([row(256, "P01")]))
    assert M.period_problems([row(0, "P01")]) == []
    assert M.period_problems([row(255, "P01")]) == []


def test_blank_code_refused():
    assert M.period_problems([row(1, "")])
    assert M.period_problems([row(1, "   ")])

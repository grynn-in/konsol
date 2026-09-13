"""Fiscal status precedence, pure: konsol/fiscal_status_model.py.

Loaded by path; the module imports nothing from frappe or konsol.
"""
import importlib.util
import os

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_spec = importlib.util.spec_from_file_location("fsm_under_test", os.path.join(APP_DIR, "fiscal_status_model.py"))
M = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(M)


def test_the_year_wins():
    O, C, L = M.OPEN, M.CLOSED, M.LOCKED
    cases = {
        (O, O): O, (O, C): C, (O, L): L,
        (C, O): C, (C, C): C, (C, L): L,
        (L, O): L, (L, C): L, (L, L): L,
    }
    for (year_status, row_status), want in cases.items():
        assert M.effective_status(year_status, row_status) == want, (year_status, row_status)


def test_unknown_status_raises():
    try:
        M.effective_status("Bogus", M.OPEN)
    except ValueError as e:
        assert "Bogus" in str(e)
    else:
        raise AssertionError("not refused")

    try:
        M.effective_status(M.OPEN, None)
    except ValueError as e:
        assert "None" in str(e)
    else:
        raise AssertionError("not refused")

    try:
        M.effective_status(M.OPEN, "")
    except ValueError:
        pass
    else:
        raise AssertionError("not refused")

"""Period strips endpoint: konsol/close/period_strip_api.py ``period_strip_info``.
Stub frappe, calendar and ch_read; the real period_strip_model, loaded by path."""
import importlib.util
import os
import sys
import types

import pytest

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLOSE_DIR = os.path.join(APP_DIR, "close")
ROLES = ("EPM Admin", "EPM Analyst", "EPM User", "System Manager")


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _periods():
    return [{"fiscal_year": fy, "fiscal_period": p, "period_type": "Regular"}
            for fy in (2024, 2025) for p in range(1, 13)]


def _invoke(ch_rows=None, ch_error=None, denied=False):
    calls = {"sql": [], "only_for": []}
    frappe = types.ModuleType("frappe")
    frappe.whitelist = lambda **kw: (lambda fn: fn)

    def only_for(roles):
        calls["only_for"].append(tuple(roles))
        if denied:
            raise PermissionError("no")

    frappe.only_for = only_for
    konsol = types.ModuleType("konsol")
    konsol.__path__ = []
    close = types.ModuleType("konsol.close")
    close.__path__ = []
    cal = types.ModuleType("konsol.fiscal_calendar")
    cal.fiscal_period_rows = _periods
    ch = types.ModuleType("konsol.close.ch_read")

    def rows(sql, params=None):
        calls["sql"].append(sql)
        if ch_error is not None:
            raise ch_error
        return ch_rows or []

    ch.rows = rows
    konsol.fiscal_calendar = cal
    konsol.close = close
    close.ch_read = ch
    names = ["frappe", "konsol", "konsol.close", "konsol.fiscal_calendar",
             "konsol.close.ch_read", "konsol.close.period_strip_model"]
    saved = {n: sys.modules.get(n) for n in names}
    sys.modules.update({"frappe": frappe, "konsol": konsol, "konsol.close": close,
                        "konsol.fiscal_calendar": cal, "konsol.close.ch_read": ch})
    try:
        close.period_strip_model = _load(
            "konsol.close.period_strip_model", os.path.join(CLOSE_DIR, "period_strip_model.py"))
        sys.modules["konsol.close.period_strip_model"] = close.period_strip_model
        api = _load("period_strip_api_under_test", os.path.join(CLOSE_DIR, "period_strip_api.py"))
        return api.period_strip_info(), calls
    finally:
        for n, m in saved.items():
            if m is None:
                sys.modules.pop(n, None)
            else:
                sys.modules[n] = m


def test_latest_with_data_from_one_warehouse_read():
    info, calls = _invoke(ch_rows=[{"fiscal_year": 2025, "fiscal_period": 5}])
    assert info["latest"] == {"fiscal_year": 2025, "fiscal_period": 5}
    assert info["years"] == [2024, 2025]
    assert len(calls["sql"]) == 1 and "gold_fully_consolidated_tb" in calls["sql"][0]
    assert calls["only_for"] == [ROLES]


def test_failed_read_falls_back_to_latest_regular_and_does_not_raise():
    info, _ = _invoke(ch_error=RuntimeError("UNKNOWN_TABLE"))
    assert info["latest"] == {"fiscal_year": 2025, "fiscal_period": 12}


def test_empty_warehouse_falls_back():
    info, _ = _invoke(ch_rows=[])
    assert info["latest"] == {"fiscal_year": 2025, "fiscal_period": 12}


def test_role_gate_applies_before_any_read():
    with pytest.raises(PermissionError):
        _invoke(denied=True)

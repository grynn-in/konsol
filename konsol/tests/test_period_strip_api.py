"""Period strips endpoint: konsol/close/period_strip_api.py ``period_strip_info``.
Stub frappe, calendar and ch_read; the real period_strip_model, loaded by path."""
import importlib.util
import os
import sys
import types

import pytest

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLOSE_DIR = os.path.join(APP_DIR, "close")
REPORTS = ("Consolidation Worksheet", "Consolidated Statement", "Statement Drill")
def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _periods():
    return [{"fiscal_year": fy, "fiscal_period": p, "period_type": "Regular"}
            for fy in (2024, 2025) for p in range(1, 13)]


class _NotFound(Exception):
    pass


def _invoke(ch_rows=None, ch_error=None, readable=REPORTS, doctype_ok=True, periods=None, **kwargs):
    """``readable``: the reports whose Report.is_permitted() is True; the rest
    are denied. ``doctype_ok``: frappe.has_permission(ref_doctype, "report")."""
    calls = {"sql": [], "params": [], "kwargs": [], "has_permission": [], "only_for": 0}
    frappe = types.ModuleType("frappe")
    frappe.whitelist = lambda **kw: (lambda fn: fn)
    frappe.PermissionError = PermissionError
    frappe.DoesNotExistError = _NotFound

    def only_for(roles):
        calls["only_for"] += 1

    frappe.only_for = only_for

    def get_doc(doctype, name):
        assert doctype == "Report"
        if name == "Statement Drill" and "missing-drill" in kwargs:
            raise _NotFound(name)
        return types.SimpleNamespace(
            name=name, ref_doctype="Trial Balance Submission",
            is_permitted=lambda: name in readable)

    def has_permission(doctype, ptype="read", *a, **kw):
        calls["has_permission"].append((doctype, ptype))
        return doctype_ok

    frappe.get_doc = get_doc
    frappe.has_permission = has_permission
    konsol = types.ModuleType("konsol")
    konsol.__path__ = []
    close = types.ModuleType("konsol.close")
    close.__path__ = []
    cal = types.ModuleType("konsol.fiscal_calendar")
    cal.fiscal_period_rows = periods or _periods
    ch = types.ModuleType("konsol.close.ch_read")

    def rows(sql, params=None, settings=None, timeout=None):
        calls["sql"].append(sql)
        calls["params"].append(params)
        calls["kwargs"].append({"settings": settings, "timeout": timeout})
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
        call_kwargs = {k: v for k, v in kwargs.items() if k == "consolidation_group"}
        return api.period_strip_info(**call_kwargs), calls
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
    assert len(calls["sql"]) == 2 and all("gold_fully_consolidated_tb" in q for q in calls["sql"])


def test_failed_read_falls_back_to_latest_regular_and_does_not_raise():
    info, _ = _invoke(ch_error=RuntimeError("UNKNOWN_TABLE"))
    assert info["latest"] == {"fiscal_year": 2025, "fiscal_period": 12}


def test_empty_warehouse_falls_back():
    info, _ = _invoke(ch_rows=[])
    assert info["latest"] == {"fiscal_year": 2025, "fiscal_period": 12}


def test_not_gated_on_fixed_roles():
    _, calls = _invoke()
    assert calls["only_for"] == 0


def test_any_one_readable_period_report_is_enough():
    info, _ = _invoke(readable=("Statement Drill",))
    assert info["years"] == [2024, 2025]


def test_no_readable_report_is_refused_before_any_read():
    with pytest.raises(PermissionError):
        _invoke(readable=())
    # report allowed, but the user lacks the report right on its doctype
    with pytest.raises(PermissionError):
        _invoke(doctype_ok=False)


def test_a_report_not_installed_is_skipped():
    info, _ = _invoke(readable=("Consolidation Worksheet",), **{"missing-drill": True})
    assert info["years"] == [2024, 2025]


def test_permission_uses_the_report_right_on_the_reports_doctype():
    _, calls = _invoke()
    assert ("Trial Balance Submission", "report") in calls["has_permission"]


def test_the_warehouse_read_has_a_short_timeout():
    _, calls = _invoke(ch_rows=[{"fiscal_year": 2025, "fiscal_period": 5}])
    assert calls["kwargs"] == [{"settings": {"max_execution_time": 2}, "timeout": 3}] * 2


def test_only_regular_periods_count_for_latest():
    # P13 (Adjustment) and an undeclared year are newer, but not Regular.
    rows = [{"fiscal_year": 2030, "fiscal_period": 1},
            {"fiscal_year": 2025, "fiscal_period": 13},
            {"fiscal_year": 2025, "fiscal_period": 0},
            {"fiscal_year": 2025, "fiscal_period": 4},
            {"fiscal_year": 2024, "fiscal_period": 12}]

    def periods():
        return _periods() + [{"fiscal_year": 2025, "fiscal_period": 13, "period_type": "Adjustment"},
                             {"fiscal_year": 2025, "fiscal_period": 0, "period_type": "Opening"}]
    info, _ = _invoke(ch_rows=rows, periods=periods)
    assert info["latest"] == {"fiscal_year": 2025, "fiscal_period": 4}


def test_no_group_means_no_group_filter():
    _, calls = _invoke(ch_rows=[{"fiscal_year": 2025, "fiscal_period": 5}])
    assert all("{group:String}" not in q for q in calls["sql"])
    assert not any(calls["params"])


def test_a_group_filters_the_read_with_a_bound_parameter():
    _, calls = _invoke(ch_rows=[{"fiscal_year": 2025, "fiscal_period": 5}],
                       consolidation_group="G1'; DROP")
    assert all("consolidation_group = {group:String}" in q for q in calls["sql"])
    assert calls["params"] == [{"group": "G1'; DROP"}] * 2
    assert not any("DROP" in q for q in calls["sql"])


def test_data_years_come_from_a_distinct_year_read():
    rows = [{"fiscal_year": 2025, "fiscal_period": 5}, {"fiscal_year": 2023, "fiscal_period": 5},
            {"fiscal_year": 2025, "fiscal_period": 1}]
    info, calls = _invoke(ch_rows=rows)
    assert info["data_years"] == [2023, 2025]
    assert info["years"] == [2024, 2025]  # declared years stay
    assert any("SELECT DISTINCT fiscal_year FROM" in q for q in calls["sql"])


def test_data_years_empty_when_the_read_fails():
    info, _ = _invoke(ch_error=RuntimeError("UNKNOWN_TABLE"))
    assert info["data_years"] == []

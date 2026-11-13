"""report_budget_dimensions_not_in_tb patch (BUD-8-2), host-run.

Lists Published Dimensions with in_budget = 1 and in_trial_balance = 0 on the
migrate log, names both fixes, and changes nothing.
"""
import contextlib
import importlib.util
import io
import os
import sys
import types

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATCH_PY = os.path.join(APP_DIR, "patches", "report_budget_dimensions_not_in_tb.py")
PATCHES_TXT = os.path.join(APP_DIR, "patches.txt")

ROWS = [
    {"name": "dim_cost_center", "status": "Published", "in_budget": 1, "in_trial_balance": 0},
    {"name": "dim_department", "status": "Published", "in_budget": 1, "in_trial_balance": 0},
    {"name": "dim_draft_only", "status": "Draft", "in_budget": 1, "in_trial_balance": 0},
    {"name": "dim_in_tb", "status": "Published", "in_budget": 1, "in_trial_balance": 1},
    {"name": "dim_not_budget", "status": "Published", "in_budget": 0, "in_trial_balance": 0},
]


def _run(source=None):
    writes = []
    calls = []
    logged = []

    def get_all(doctype, filters=None, fields=None, **kw):
        assert doctype == "Dimension"
        calls.append(filters)
        rows = [r for r in (ROWS if source is None else source) if all(r.get(k) == v for k, v in (filters or {}).items())]
        return [{"name": r["name"]} for r in rows]

    def forbidden(name):
        def f(*a, **k):
            writes.append(name)
        return f

    frappe = types.ModuleType("frappe")
    frappe.get_all = get_all
    frappe.get_list = get_all
    frappe.log_error = lambda *a, **k: logged.append((a, k))
    frappe.get_doc = forbidden("get_doc")
    frappe.delete_doc = forbidden("delete_doc")
    frappe.db = types.SimpleNamespace(
        set_value=forbidden("set_value"), sql=forbidden("sql"),
        delete=forbidden("delete"), commit=forbidden("commit"),
    )
    saved = sys.modules.get("frappe")
    sys.modules["frappe"] = frappe
    try:
        spec = importlib.util.spec_from_file_location("report_budget_dims_under_test", PATCH_PY)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            mod.execute()
    finally:
        if saved is None:
            sys.modules.pop("frappe", None)
        else:
            sys.modules["frappe"] = saved
    return buf.getvalue(), writes, calls, logged


def test_names_both_offenders_and_both_fixes():
    out = _run()[0]
    assert "dim_cost_center" in out
    assert "dim_department" in out
    assert "Include in Trial Balance" in out
    assert "Include in Budget" in out
    assert "unpublish" in out


def test_filter_is_exactly_published_budget_not_tb():
    calls = _run()[2]
    assert calls == [{"status": "Published", "in_budget": 1, "in_trial_balance": 0}]


def _flat(entry):
    a, k = entry
    return " ".join(str(x) for x in a) + " " + " ".join(str(v) for v in k.values())


def test_log_error_called_with_both_offenders():
    logged = _run()[3]
    assert len(logged) == 1
    text = _flat(logged[0])
    assert "dim_cost_center" in text and "dim_department" in text
    assert logged[0][1].get("title") and logged[0][1].get("message")


def test_log_error_not_called_when_no_offenders():
    logged = _run(source=[])[3]
    assert logged == []


def test_unpublished_and_compliant_dimensions_not_named():
    out = _run()[0]
    assert "dim_draft_only" not in out
    assert "dim_in_tb" not in out
    assert "dim_not_budget" not in out


def test_no_write_call():
    writes = _run()[1]
    assert writes == []


def test_registered_in_patches_txt():
    with open(PATCHES_TXT) as f:
        assert "konsol.patches.report_budget_dimensions_not_in_tb" in f.read()

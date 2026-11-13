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


def _run():
    writes = []

    def get_all(doctype, filters=None, fields=None, **kw):
        assert doctype == "Dimension"
        rows = [r for r in ROWS if all(r.get(k) == v for k, v in (filters or {}).items())]
        return [{"name": r["name"]} for r in rows]

    def forbidden(name):
        def f(*a, **k):
            writes.append(name)
        return f

    frappe = types.ModuleType("frappe")
    frappe.get_all = get_all
    frappe.get_list = get_all
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
    return buf.getvalue(), writes


def test_names_both_offenders_and_both_fixes():
    out, _ = _run()
    assert "dim_cost_center" in out
    assert "dim_department" in out
    assert "In Trial Balance" in out
    assert "In Budget" in out


def test_unpublished_and_compliant_dimensions_not_named():
    out, _ = _run()
    assert "dim_draft_only" not in out
    assert "dim_in_tb" not in out
    assert "dim_not_budget" not in out


def test_no_write_call():
    _, writes = _run()
    assert writes == []


def test_registered_in_patches_txt():
    with open(PATCHES_TXT) as f:
        assert "konsol.patches.report_budget_dimensions_not_in_tb" in f.read()

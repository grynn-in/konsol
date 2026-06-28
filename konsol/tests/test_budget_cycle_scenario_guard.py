"""Budget Cycle must not target an 'actual' scenario.

Site-free, matching the repo convention (see test_budget_cycle_reshape): AST /
source assertions that the server-side guard and the client picker filter are
wired. Live DB rejection is exercised by ``bench run-tests`` against a site.
"""
import ast
import os

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CYCLE_PY = os.path.join(APP_DIR, "epm", "doctype", "budget_cycle", "budget_cycle.py")
CYCLE_JS = os.path.join(APP_DIR, "epm", "doctype", "budget_cycle", "budget_cycle.js")


def _src(path):
    with open(path) as fh:
        return fh.read()


def _method(src, name):
    for n in ast.walk(ast.parse(src)):
        if isinstance(n, ast.FunctionDef) and n.name == name:
            return ast.get_source_segment(src, n)
    return None


def test_validate_method_exists():
    assert _method(_src(CYCLE_PY), "validate") is not None


def test_validate_rejects_actual_scenario():
    validate = _method(_src(CYCLE_PY), "validate")
    # reads the linked scenario's type and throws on actual
    assert "scenario_type" in validate
    assert '"actual"' in validate or "'actual'" in validate
    assert "frappe.throw" in validate


def test_js_picker_filters_out_actual():
    js = _src(CYCLE_JS)
    assert "set_query" in js
    assert "scenario_id" in js
    assert "scenario_type" in js
    assert "actual" in js

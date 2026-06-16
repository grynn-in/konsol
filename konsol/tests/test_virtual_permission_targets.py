"""Structural tests for the budget permission-target Virtual DocTypes (#51 §B).

Site-free: verifies each virtual doctype satisfies Frappe's validate_controller
contract (static get_list/get_count/get_stats; read-only instance methods
db_insert/db_update/load_from_db/delete defined ON the class, since the
validator compares against mro()[1]) and that the JSON marks them virtual.
"""
import ast
import json
import os

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOCTYPE_DIR = os.path.join(APP_DIR, "epm", "doctype")

VIRTUAL_DOCTYPES = [
    ("main_account_category", "MainAccountCategory", "Main Account Category"),
    ("budget_cost_center", "BudgetCostCenter", "Budget Cost Center"),
]

_STATIC = {"get_list", "get_count", "get_stats"}
_INSTANCE = {"db_insert", "db_update", "load_from_db", "delete"}


def _class_node(py_path, cls_name):
    with open(py_path) as fh:
        tree = ast.parse(fh.read())
    for n in ast.walk(tree):
        if isinstance(n, ast.ClassDef) and n.name == cls_name:
            return n
    return None


def _methods(cls_node):
    """{name: is_static} for methods defined directly on the class."""
    out = {}
    for n in cls_node.body:
        if isinstance(n, ast.FunctionDef):
            is_static = any(isinstance(d, ast.Name) and d.id == "staticmethod"
                            for d in n.decorator_list)
            out[n.name] = is_static
    return out


def test_virtual_doctypes_satisfy_controller_contract():
    for folder, cls_name, _label in VIRTUAL_DOCTYPES:
        py = os.path.join(DOCTYPE_DIR, folder, f"{folder}.py")
        cls = _class_node(py, cls_name)
        assert cls is not None, f"{cls_name} class missing"
        methods = _methods(cls)
        # static query methods present AND decorated @staticmethod
        for m in _STATIC:
            assert methods.get(m) is True, f"{cls_name}.{m} must be a staticmethod"
        # read-only instance methods defined ON the class (not inherited),
        # else Frappe's validator (mro()[1] compare) flags them.
        for m in _INSTANCE:
            assert m in methods and methods[m] is False, f"{cls_name}.{m} must be an instance method"


def test_virtual_doctype_json_is_virtual_and_named():
    for folder, _cls, label in VIRTUAL_DOCTYPES:
        meta = json.load(open(os.path.join(DOCTYPE_DIR, folder, f"{folder}.json")))
        assert meta.get("is_virtual") == 1, f"{label} must be is_virtual=1"
        assert meta.get("name") == label
        assert meta.get("module") == "EPM"


def test_cost_center_target_avoids_erpnext_collision():
    """ERPNext owns 'Cost Center'; our target must be 'Budget Cost Center'."""
    meta = json.load(open(os.path.join(DOCTYPE_DIR, "budget_cost_center", "budget_cost_center.json")))
    assert meta["name"] == "Budget Cost Center"


def test_targets_are_read_only_via_clickhouse():
    """Value source is ClickHouse (silver layer), proxied — no synced table."""
    mac = open(os.path.join(DOCTYPE_DIR, "main_account_category", "main_account_category.py")).read()
    bcc = open(os.path.join(DOCTYPE_DIR, "budget_cost_center", "budget_cost_center.py")).read()
    assert "epm_silver.silver_main_accounts" in mac
    assert "epm_silver.silver_financial_dimensions" in bcc
    assert "readonly_guard" in mac and "readonly_guard" in bcc

"""Tests for budget write-path authorization (spec #51 §B).

Site-free, matching the repo convention: AST/source assertions that the
authorization layer exists and is invoked on every write path, plus structural
checks on the new permission-target config fields.
"""
import ast
import json
import os

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
API_PATH = os.path.join(APP_DIR, "api.py")
EPM_SETTINGS_JSON = os.path.join(APP_DIR, "pipeline", "doctype", "epm_settings", "epm_settings.json")
DIMENSION_JSON = os.path.join(APP_DIR, "epm", "doctype", "dimension", "dimension.json")


def _api_tree():
    with open(API_PATH) as fh:
        return ast.parse(fh.read())


def _func(tree, name):
    for n in ast.walk(tree):
        if isinstance(n, ast.FunctionDef) and n.name == name:
            return n
    return None


def _calls_in(func):
    """Names of functions called within `func` (handles bare and attr calls)."""
    out = set()
    for n in ast.walk(func):
        if isinstance(n, ast.Call):
            f = n.func
            if isinstance(f, ast.Name):
                out.add(f.id)
            elif isinstance(f, ast.Attribute):
                out.add(f.attr)
    return out


# --- the authorization layer exists ----------------------------------------

def test_authorization_helpers_exist():
    tree = _api_tree()
    for name in ("_assert_budget_write_access", "_assert_account_access",
                 "_assert_dimension_access", "_account_permission_doctype",
                 "_permission_controlled_dimensions", "_account_category"):
        assert _func(tree, name) is not None, f"missing {name}"


def test_composite_check_covers_entity_account_and_dimensions():
    calls = _calls_in(_func(_api_tree(), "_assert_budget_write_access"))
    assert {"_assert_entity_access", "_assert_account_access",
            "_assert_dimension_access"} <= calls


# --- every write path enforces ---------------------------------------------

def test_save_and_batch_go_through_the_cell_path():
    """S2-X2: budget_save and budget_save_batch write each period through
    _save_budget_cell, the path budget_cell_save uses, so the access check runs
    in budget_intake for every cell."""
    tree = _api_tree()
    assert "_save_budget_cell" in _calls_in(_func(tree, "_budget_save_cells"))
    assert "_budget_save_cells" in _calls_in(_func(tree, "budget_save"))
    assert "_budget_save_cells" in _calls_in(_func(tree, "budget_save_batch"))
    assert "_save_budget_cell" in _calls_in(_func(tree, "budget_cell_save"))


def test_cell_save_enforces_write_access():
    """S2-X1: the cell goes through budget_intake (judge_one, which calls
    plan), which runs _assert_budget_write_access on every row
    (test_budget_intake.py)."""
    tree = _api_tree()
    assert "_judge_budget_cell" in _calls_in(_func(tree, "_save_budget_cell"))
    assert "judge_one" in _calls_in(_func(tree, "_judge_budget_cell"))


def test_batch_reports_permission_errors_per_item():
    """budget_save_batch keeps a PermissionError a PermissionError, and names the row."""
    src = ast.get_source_segment(open(API_PATH).read(), _func(_api_tree(), "budget_save_batch"))
    assert "frappe.PermissionError" in src and "type(e)" in src and "Row {i + 1}" in src
    assert src.index("frappe.PermissionError") < src.index("Budget save failed")


# --- account-category resolution is cached, off the write hot path ----------

def test_account_category_uses_cache():
    src = ast.get_source_segment(open(API_PATH).read(), _func(_api_tree(), "_account_category"))
    assert "frappe.cache()" in src and "set_value" in src


def test_account_access_is_optional_and_fails_closed():
    src = ast.get_source_segment(open(API_PATH).read(), _func(_api_tree(), "_assert_account_access"))
    assert "if not perm_doctype" in src  # no-op when unconfigured
    assert "category not in allowed" in src or "category is None" in src  # fail-closed


# --- permission-target config fields ---------------------------------------

def test_epm_settings_has_account_permission_doctype():
    meta = json.load(open(EPM_SETTINGS_JSON))
    fields = {f["fieldname"]: f for f in meta["fields"]}
    assert "account_permission_doctype" in fields
    assert fields["account_permission_doctype"]["fieldtype"] == "Link"
    assert fields["account_permission_doctype"]["options"] == "DocType"


def test_dimension_has_permission_doctype():
    meta = json.load(open(DIMENSION_JSON))
    fields = {f["fieldname"]: f for f in meta["fields"]}
    assert "permission_doctype" in fields
    assert fields["permission_doctype"]["fieldtype"] == "Link"


def test_get_user_permissions_always_passed_a_user():
    """Regression: frappe.permissions.get_user_permissions(user) requires the
    `user` arg (v15) — a no-arg call 500s every gated read/write at runtime,
    which the site-free tests otherwise can't catch."""
    src = open(API_PATH).read()
    assert "get_user_permissions()" not in src, \
        "get_user_permissions must be called with a user argument"

"""Entity-scoped access (issue #91) — structural tests.

Behavioural counterpart: test_entity_permissions_bench.py.
"""
import ast
import os

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _src(rel):
    with open(os.path.join(APP_DIR, rel)) as f:
        return f.read()


def test_module_parses():
    ast.parse(_src("entity_permissions.py"))


def test_hooks_register_query_conditions_for_every_scoped_doctype():
    """There were none at all before, so desk lists were never filtered."""
    hooks = _src("hooks.py")
    assert "permission_query_conditions" in hooks
    assert "ENTITY_SCOPED_DOCTYPES" in hooks


def test_hooks_register_has_permission():
    """Query conditions cover lists; has_permission covers opening one doc."""
    hooks = _src("hooks.py")
    assert "has_permission" in hooks
    assert "has_entity_permission" in hooks


def test_entity_itself_is_scoped():
    hooks = _src("hooks.py")
    assert "entity_conditions" in hooks


def test_admins_bypass():
    """Without this an admin locks themselves out by self-assigning one entity."""
    src = _src("entity_permissions.py")
    assert "BYPASS_ROLES" in src
    assert "System Manager" in src


def test_empty_grant_denies_rather_than_permits():
    """An empty allow-set must emit a false condition. Returning "" would mean
    "no restriction" — the exact inverse of what an empty grant means."""
    src = _src("entity_permissions.py")
    assert "1=0" in src


def test_subtree_expansion_uses_the_nested_set():
    """Assignment to a region has to carry its subsidiaries; that is why
    Entity is a tree rather than a flat list."""
    src = _src("entity_permissions.py")
    assert "lft" in src and "rgt" in src


def test_codes_are_escaped_into_sql():
    src = _src("entity_permissions.py")
    assert "frappe.db.escape" in src


def test_clickhouse_paths_still_guarded_separately():
    """permission_query_conditions constrain Frappe queries only; the
    warehouse reads bypass Frappe entirely."""
    src = _src("entity_permissions.py")
    assert "assert_entity_access" in src
    api = _src("api.py")
    assert "assert_entity_access" in api


def test_config_indirection_is_retired():
    """entity_permission_doctype existed only because there was no Entity to
    point at, and it meant the feature was off unless someone set it."""
    api = _src("api.py")
    assert "_entity_permission_doctype()" not in api, (
        "api.py should no longer resolve the permission target from settings"
    )


def test_absent_permission_still_means_unrestricted_by_default():
    """A deliberate deviation from issue #91: in Frappe a User Permission is an
    opt-in restriction, and inverting that would lock out every existing site
    on upgrade. Deny-by-default is available, but opt-in."""
    src = _src("entity_permissions.py")
    assert "_restrict_by_default" in src
    assert "restrict_entities_by_default" in src


def test_strict_mode_setting_exists():
    import json
    p = os.path.join(APP_DIR, "pipeline", "doctype", "epm_settings", "epm_settings.json")
    with open(p) as f:
        d = json.load(f)
    names = {x["fieldname"] for x in d["fields"]}
    assert "restrict_entities_by_default" in names

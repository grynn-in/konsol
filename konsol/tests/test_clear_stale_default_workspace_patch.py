"""A User whose default_workspace names a deleted Workspace is repaired (#530).

``delete_konsolidat_workspace`` deleted Workspace "Konsolidat" and left every
User who had it as ``default_workspace`` pointing at it. Any full save of such
a User then failed with ``LinkValidationError: Could not find Default
Workspace: Konsolidat``: a role change, a profile edit, a patch calling
``User.add_roles``. Three demo users were affected on the test site.

The patch sets ``default_workspace`` to NULL on every User whose value names
a Workspace that does not exist, whatever its name, and leaves a valid value
alone. It never saves the User: that is the call that fails. A rerun changes
nothing. The patch runs here against a stub frappe that models a site.
"""
import importlib.util
import os
import sys
import types

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATCH_PY = os.path.join(APP_DIR, "patches", "clear_stale_default_workspace.py")
DOTTED = "konsol.patches.clear_stale_default_workspace"


class _Site:
    def __init__(self, workspaces, users):
        self.workspaces = set(workspaces)
        self.users = dict(users)  # user -> default_workspace (None when unset)
        self.set_value_calls = []
        self.saved = []

    def frappe(self):
        site = self
        frappe = types.ModuleType("frappe")

        def exists(doctype, name=None, *a, **k):
            assert doctype == "Workspace", doctype
            return name if name in site.workspaces else None

        def get_all(doctype, filters=None, fields=None, pluck=None, order_by=None, **k):
            assert doctype == "User", doctype
            assert order_by, "a result that drives order needs order_by (v16 sorts by creation)"
            rows = []
            for user, ws in sorted(site.users.items()):
                if filters.get("default_workspace") == ["is", "set"]:
                    if not ws:
                        continue
                elif "default_workspace" in filters:
                    if ws != filters["default_workspace"]:
                        continue
                if pluck:
                    rows.append(user if pluck == "name" else ws)
                else:
                    rows.append({"name": user, "default_workspace": ws})
            return rows

        def set_value(doctype, dn, field, value, *a, update_modified=True, **k):
            assert doctype == "User" and field == "default_workspace", (doctype, field)
            assert update_modified is False, "a repair must not touch modified"
            site.set_value_calls.append((dn, value))
            targets = dn["name"][1] if isinstance(dn, dict) else [dn]
            for user in targets:
                site.users[user] = value

        def get_doc(*a, **k):
            raise AssertionError("the patch must not load or save a User: that save is what fails")

        frappe.db = types.SimpleNamespace(exists=exists, set_value=set_value)
        frappe.get_all = get_all
        frappe.get_doc = get_doc
        return frappe


def _run(site):
    saved = sys.modules.get("frappe")
    sys.modules["frappe"] = site.frappe()
    try:
        spec = importlib.util.spec_from_file_location("_patch_under_test", PATCH_PY)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        mod.execute()
    finally:
        if saved is None:
            sys.modules.pop("frappe", None)
        else:
            sys.modules["frappe"] = saved
    return site


def test_the_patch_is_registered_once():
    with open(os.path.join(APP_DIR, "patches.txt")) as f:
        lines = f.read().split()
    assert lines.count(DOTTED) == 1


def test_a_stale_value_is_cleared_and_a_valid_one_kept(capsys):
    site = _run(_Site(
        workspaces={"Close", "Home"},
        users={"a@example.com": "Konsolidat", "b@example.com": "Konsolidat",
               "c@example.com": "Budget", "d@example.com": "Close", "e@example.com": None},
    ))
    assert site.users == {"a@example.com": None, "b@example.com": None, "c@example.com": None,
                          "d@example.com": "Close", "e@example.com": None}
    out = capsys.readouterr().out
    assert "Konsolidat" in out and "a@example.com" in out and "b@example.com" in out
    assert "Budget" in out and "c@example.com" in out
    assert "d@example.com" not in out


def test_a_rerun_is_a_no_op():
    site = _run(_Site(workspaces={"Close"}, users={"a@example.com": "Konsolidat", "d@example.com": "Close"}))
    assert site.set_value_calls, "the first run clears a@example.com"
    site.set_value_calls.clear()
    _run(site)
    assert site.set_value_calls == []
    assert site.users == {"a@example.com": None, "d@example.com": "Close"}


def test_a_site_with_no_stale_value_is_left_alone():
    site = _run(_Site(workspaces={"Close"}, users={"d@example.com": "Close", "e@example.com": None}))
    assert site.set_value_calls == []

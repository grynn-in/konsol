"""The Konsolidat workspace and the old Konsol sidebar leave existing sites (Desk IA, 7 Nov 2026).

The Desk IA (Deepak Pai, 7 Nov 2026) gives each rail entry one sidebar and one
page with one label per doctype. Workspace "Konsolidat" (module EPM) is the
third copy of every link, under a third set of labels, and on konsolidat.local
it still linked the deleted doctype Budget Cost Center. It is not a shipped file:
``konsol.install.after_migrate`` rebuilt it from ``konsol/dashboard.py`` on every
migrate. So the patch alone would be undone by the next migrate: after_migrate
must stop rebuilding it, and the patch deletes the copy a site already has.

Sidebar "Konsol" became sidebar "Close". Frappe's orphan sweep removes a
standard Sidebar whose file left the app; the patch deletes it too, so a site
whose row is not marked standard loses it as well. Deleting a missing record
is a no-op.

The patch runs here against a stub frappe that models a site.
"""
import ast
import importlib.util
import os
import sys
import types

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATCH_PY = os.path.join(APP_DIR, "patches", "delete_konsolidat_workspace.py")
DOTTED = "konsol.patches.delete_konsolidat_workspace"

KONSOLIDAT = ("Workspace", "Konsolidat")
OLD_SIDEBAR = ("Sidebar", "Konsol")


class _Site:
    def __init__(self, records):
        # (doctype, name) -> the row's module
        self.records = dict(records)
        self.deleted = []

    def frappe(self):
        site = self
        frappe = types.ModuleType("frappe")

        def exists(doctype, name=None, *a, **k):
            return name if (doctype, name) in site.records else None

        def get_value(doctype, name, field, *a, **k):
            assert field == "module", field
            return site.records.get((doctype, name))

        def delete_doc(doctype, name, force=False, ignore_permissions=False, **k):
            if (doctype, name) not in site.records:
                raise AssertionError("delete_doc on a missing record: %s %s" % (doctype, name))
            site.deleted.append((doctype, name))
            del site.records[(doctype, name)]

        frappe.db = types.SimpleNamespace(exists=exists, get_value=get_value)
        frappe.delete_doc = delete_doc
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


def test_the_patch_is_registered():
    with open(os.path.join(APP_DIR, "patches.txt")) as f:
        assert DOTTED in f.read().split()


def test_it_deletes_the_konsolidat_workspace_and_the_old_sidebar():
    site = _run(_Site({KONSOLIDAT: "EPM", OLD_SIDEBAR: "Consolidation",
                       ("Workspace", "Konsol"): "Consolidation"}))
    assert sorted(site.deleted) == sorted([KONSOLIDAT, OLD_SIDEBAR])
    assert ("Workspace", "Konsol") in site.records


def test_a_site_without_them_is_left_alone():
    site = _run(_Site({("Workspace", "Konsol"): "Consolidation"}))
    assert site.deleted == []


def test_a_record_of_the_same_name_from_another_module_is_kept():
    """Only konsol's own copies go: a site's own workspace called Konsolidat in
    another module is the site's."""
    site = _run(_Site({KONSOLIDAT: "Selling", OLD_SIDEBAR: "Selling"}))
    assert site.deleted == []


def _function(path, name):
    with open(path) as f:
        tree = ast.parse(f.read())
    return next((n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name), None)


def test_after_migrate_no_longer_rebuilds_the_konsolidat_workspace():
    """Without this the next migrate recreates what the patch deleted."""
    install = os.path.join(APP_DIR, "install.py")
    body = _function(install, "after_migrate")
    assert body is not None
    called = {ast.unparse(n.func) for n in ast.walk(body) if isinstance(n, ast.Call)}
    assert "_setup_dashboard" not in called, "after_migrate still rebuilds Konsolidat"
    assert _function(install, "_setup_dashboard") is None

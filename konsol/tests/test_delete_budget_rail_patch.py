"""The Budget sidebar and page leave existing sites: Planning replaced them (#443).

Decided by Deepak Pai, 7 Nov 2026, option B on #443 (rejected A, separate
Budget and Forecast rail entries; C, Forecast doctypes; D, unfiltered
duplicates): the rail entry Budget became Planning. The shipped files for
Sidebar "Budget" and Workspace "Budget" are gone; the patch deletes a site's
copies, only konsol's own (module EPM). Deleting a missing record is a no-op.

The patch runs here against a stub frappe that models a site.
"""
import importlib.util
import os
import sys
import types

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATCH_PY = os.path.join(APP_DIR, "patches", "delete_budget_rail.py")
DOTTED = "konsol.patches.delete_budget_rail"

SIDEBAR = ("Sidebar", "Budget")
WORKSPACE = ("Workspace", "Budget")


class _Site:
    def __init__(self, records):
        self.records = dict(records)  # (doctype, name) -> module
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


def test_the_patch_is_registered_once():
    with open(os.path.join(APP_DIR, "patches.txt")) as f:
        assert f.read().split().count(DOTTED) == 1


def test_it_deletes_the_budget_sidebar_and_page():
    site = _run(_Site({SIDEBAR: "EPM", WORKSPACE: "EPM", ("Sidebar", "Planning"): "EPM"}))
    assert sorted(site.deleted) == sorted([SIDEBAR, WORKSPACE])
    assert ("Sidebar", "Planning") in site.records


def test_a_site_without_them_is_left_alone():
    site = _run(_Site({("Sidebar", "Planning"): "EPM"}))
    assert site.deleted == []


def test_a_record_of_the_same_name_from_another_module_is_kept():
    """Only konsol's own copies go: a site's own Budget workspace elsewhere is the site's."""
    site = _run(_Site({SIDEBAR: "Accounts", WORKSPACE: "Accounts"}))
    assert site.deleted == []

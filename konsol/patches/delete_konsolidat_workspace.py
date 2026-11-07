"""Remove the Konsolidat workspace and the old Konsol sidebar (Desk IA, 7 Nov 2026).

The Desk IA (Deepak Pai, 7 Nov 2026) gives each rail entry one sidebar and one
page, with one label per doctype. Workspace "Konsolidat" was a third copy of
every link under a third set of labels; ``after_migrate`` no longer rebuilds it
from ``konsol/dashboard.py``, so this deletes the copy a site already has.
Sidebar "Konsol" is now sidebar "Close". Only konsol's own records go, and a
missing record is skipped (konsol/tests/test_delete_konsolidat_workspace_patch.py).
"""
import frappe

RETIRED = (
    ("Workspace", "Konsolidat", "EPM"),
    ("Sidebar", "Konsol", "Consolidation"),
)


def execute():
    for doctype, name, module in RETIRED:
        if not frappe.db.exists(doctype, name):
            continue
        if frappe.db.get_value(doctype, name, "module") != module:
            continue
        frappe.delete_doc(doctype, name, force=True, ignore_permissions=True)

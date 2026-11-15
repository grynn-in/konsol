"""Remove the Konsolidat workspace and the old Konsol sidebar (Desk IA, 7 Nov 2026).

The Desk IA (Deepak Pai, 7 Nov 2026) gives each rail entry one sidebar and one
page, with one label per doctype. Workspace "Konsolidat" was a third copy of
every link under a third set of labels; ``after_migrate`` no longer rebuilds it
from ``konsol/dashboard.py``, so this deletes the copy a site already has.
Sidebar "Konsol" is now sidebar "Close". Only konsol's own records go, and a
missing record is skipped (konsol/tests/test_delete_konsolidat_workspace_patch.py).
"""
import frappe

from konsol.patches.clear_stale_default_workspace import clear_default_workspace

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
        if doctype == "Workspace":
            # A User whose default_workspace named the deleted Workspace could
            # not be saved at all afterwards (#530). Sites that ran this patch
            # before the clearing was added are repaired by
            # clear_stale_default_workspace, which runs later in patches.txt.
            clear_default_workspace(name)
        frappe.delete_doc(doctype, name, force=True, ignore_permissions=True)

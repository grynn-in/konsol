"""Remove the Approvals Desk report (5 Nov 2026).

Approvals moved into each record's Actions menu, as in the compliance app
(Deepak Pai, 5 Nov 2026; konsol/tests/test_desk_approval_actions.py). Frappe
does not delete a standard Report whose folder left the app, so its sidebar
and search entries would open a report with no code behind it.
"""
import frappe


def execute():
    if frappe.db.exists("Report", "Approvals"):
        if frappe.db.get_value("Report", "Approvals", "module") == "Consolidation":
            frappe.delete_doc("Report", "Approvals", force=True, ignore_permissions=True)

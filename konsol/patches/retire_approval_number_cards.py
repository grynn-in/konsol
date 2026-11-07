"""Remove the four Number Cards the Konsol home close band replaced (konsol#407).

The band's Custom cards (konsol/close/overview_api.py) show trial balances in
against expected and one "Waiting for my approval" count; the approval lists
stay reachable through the workspace's shortcuts and the sidebar. Frappe does
not delete a standard Number Card whose folder left the app, so without this
the retired cards stay in search and in "Add number card". Only the app's own
standard cards are deleted.
"""
import frappe

RETIRED = (
    "Trial Balances Submitted",
    "Rates Awaiting Approval",
    "Equity Rates Awaiting Approval",
    "Journals Awaiting Approval",
)


def execute():
    for name in RETIRED:
        row = frappe.db.get_value("Number Card", name, ["module", "is_standard"], as_dict=True)
        if row and row.module == "Consolidation" and row.is_standard:
            frappe.delete_doc("Number Card", name, force=True, ignore_permissions=True)

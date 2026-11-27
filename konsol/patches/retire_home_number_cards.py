"""Remove the six home Number Cards the Konsol Close Band block replaced (konsol#610).

Decided by Deepak Pai, 26 Nov 2026 (option B): Frappe 16.60 draws a Custom
number card's value as text, so these cards printed their HTML, and a Custom
card cannot carry text, a route and a per-state colour together. The band is
now the "Konsol Close Band" Custom HTML Block (overview_api.get_close_band),
drawn inside the Konsol Home Tabs block. Frappe does not delete a standard
Number Card whose folder left the app, so without this the retired cards stay
in search and in "Add number card", calling methods that no longer exist.
Only the app's own standard cards are deleted; a site's own card of the same
name (not standard, or another module) is left alone.
"""
import frappe

RETIRED = (
    "Close Status",
    "Trial Balances In",
    "Waiting for My Approval",
    "IC Pairs over Tolerance",
    "TB Exceptions Declared",
    "Warehouse Freshness",
)


def execute():
    for name in RETIRED:
        row = frappe.db.get_value("Number Card", name, ["module", "is_standard"], as_dict=True)
        if row and row.module == "Consolidation" and row.is_standard:
            frappe.delete_doc("Number Card", name, force=True, ignore_permissions=True)

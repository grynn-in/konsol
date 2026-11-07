"""Remove the Budget sidebar and page: Planning replaced them (#443).

Decided by Deepak Pai, 7 Nov 2026, option B on #443 (rejected A, separate
Budget and Forecast rail entries; C, Forecast doctypes; D, unfiltered
duplicates): the rail entry Budget became Planning, one sidebar listing budgets
and forecasts apart. Only konsol's own records (module EPM) go, and a missing
record is skipped (konsol/tests/test_delete_budget_rail_patch.py).
"""
import frappe

RETIRED = (
    ("Sidebar", "Budget", "EPM"),
    ("Workspace", "Budget", "EPM"),
)


def execute():
    for doctype, name, module in RETIRED:
        if not frappe.db.exists(doctype, name):
            continue
        if frappe.db.get_value(doctype, name, "module") != module:
            continue
        frappe.delete_doc(doctype, name, force=True, ignore_permissions=True)

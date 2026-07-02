"""Retire the deprecated ``Budget Input`` doctypes (PRD-08).

``Budget Input`` (+ ``Budget Input Child`` and its per-doc workflow) was the
pre-reshape budget entry point. It has been explicitly deprecated since the
pivot to the live chain Budget Cycle → Budget Sheet → Budget Line
(``reshape_budget_input_to_cycle``), its lifecycle hooks were no-ops and
``api.py`` never referenced it. This patch is the deliberate cutover step the
reshape left open: it verifies the reshape actually migrated the data, then
deletes the doctypes and drops their tables (Frappe's ``delete_doc`` on a
DocType removes the meta but deliberately leaves the table behind — the drop
must be explicit). The reshape patch stays in patches.txt as the audit trail
of the migration (it runs before this patch and is guarded for fresh
installs).
"""
import frappe


def execute():
    # Parity gate: never delete unmigrated budget data. If old Budget Input
    # rows exist but the reshape never produced a single Budget Sheet, the
    # table below would be the only copy of that data — abort the migrate
    # instead of dropping it.
    if frappe.db.table_exists("Budget Input"):
        input_rows = frappe.db.count("Budget Input")
        sheet_rows = (
            frappe.db.count("Budget Sheet")
            if frappe.db.table_exists("Budget Sheet")
            else 0
        )
        if input_rows and not sheet_rows:
            frappe.throw(
                "retire_budget_input: tabBudget Input still has {0} row(s) "
                "but tabBudget Sheet is empty — the "
                "reshape_budget_input_to_cycle migration has not run (or "
                "produced nothing), so deleting now would destroy unmigrated "
                "budget data. Run the reshape and verify the Budget Cycle / "
                "Sheet / Line data before retiring.".format(input_rows)
            )

    frappe.delete_doc(
        "Workflow", "Budget Input Workflow", force=True, ignore_missing=True
    )
    frappe.delete_doc(
        "DocType", "Budget Input Child", force=True, ignore_missing=True
    )
    frappe.delete_doc(
        "DocType", "Budget Input", force=True, ignore_missing=True
    )

    # delete_doc leaves the data tables in place; drop them explicitly (the
    # parity gate above proved the data was migrated — child table first).
    frappe.db.sql_ddl("DROP TABLE IF EXISTS `tabBudget Input Child`")
    frappe.db.sql_ddl("DROP TABLE IF EXISTS `tabBudget Input`")

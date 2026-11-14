"""Reshape Budget Input → Budget Cycle / Sheet / Line: retired, kept as a guard.

This patch once pivoted the pre-reshape ``Budget Input`` rows (one doc per
scenario, entity, year and account, with a ``periods`` child table) into
Budget Cycle / Budget Sheet / Budget Line. konsol#348 S2-C Z1 retired Sheet
and Line and the grain helpers the pivot used, so there is nothing left to
pivot into, and its cutover helpers (the D365 and ClickHouse purges) went
with the D365 write-back (Z4a) and ``drop_budget_monthly_input``.

What is left:

- Rows still in ``tabBudget Input`` or ``tabBudget Input Child`` refuse the
  migrate, naming both counts. The operator exports or deletes them (budgets
  are now entered as Budget Entries; nothing migrates Budget Input rows into
  them), then migrates again. The refusal leaves the patch unmarked, so it
  runs again on that migrate.
- Without either table, or with both empty, it does nothing. That is every
  fresh install, and every site the old pivot already ran on, since
  ``retire_budget_input`` drops both tables after it.
"""
import frappe

#: Parent first: the counts are named in this order.
TABLES = ("Budget Input", "Budget Input Child")


class BudgetInputRowsRemain(Exception):
    """Budget Input rows remain, and nothing can take them any more."""


def execute():
    remaining = [(frappe.db.count(doctype), doctype) for doctype in TABLES
                 if frappe.db.table_exists(doctype)]
    remaining = [(rows, doctype) for rows, doctype in remaining if rows]
    if remaining:
        counts = ", ".join(f"{rows} {doctype}" for rows, doctype in remaining)
        raise BudgetInputRowsRemain(
            f"konsol#348 S2-C: {counts} row(s) remain. Budget Input is retired, and Budget Sheet and "
            "Budget Line, which this patch pivoted it into, are retired too: nothing can take these rows. "
            "Export or delete them (budgets are now entered as Budget Entries), then run migrate again.")

"""Normalize legacy Pipeline Step status 'Failure' -> 'Failed' (#67 fix 6).

The Pipeline Step status vocabulary was unified on 'Failed' (#65c-i / #66) and
'Failure' was dropped from the doctype Select options. Old rows written before
that still carry 'Failure', which ``control_api._step_state`` no longer maps and
renders as 'pending'. This one-shot UPDATE rewrites them. Idempotent: re-running
matches nothing once the rows are normalized; guarded so it's a no-op if the
table/column doesn't exist yet.
"""
import frappe


def execute():
    if not frappe.db.table_exists("Pipeline Step"):
        return
    try:
        columns = frappe.db.get_table_columns("Pipeline Step")
    except Exception:
        columns = []
    if "status" not in columns:
        return
    frappe.db.sql(
        "UPDATE `tabPipeline Step` SET status='Failed' WHERE status='Failure'"
    )
    frappe.db.commit()

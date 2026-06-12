"""Set status=Published for all existing Dimension and Measure records.

Existing records pre-date the lifecycle feature and should be treated as Published.
"""
import frappe


def execute():
    for doctype in ("Dimension", "Measure"):
        table = f"tab{doctype}"
        frappe.db.sql(
            "UPDATE `{table}` SET status = %s WHERE status IS NULL OR status = '' OR status = 'Draft'".format(table=table),
            ("Published",),
        )
    frappe.db.commit()

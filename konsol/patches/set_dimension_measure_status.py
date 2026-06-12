"""Set status=Published for all existing Dimension and Measure records.

Existing records pre-date the lifecycle feature and should be treated as Published.
"""
import frappe


def execute():
    for doctype in ("Dimension", "Measure"):
        frappe.db.sql(
            f"UPDATE `tab{doctype}` SET status = 'Published' WHERE status IS NULL OR status = '' OR status = 'Draft'"
        )
    frappe.db.commit()

"""Drop the unused ``Dimension.allocation_role`` column (#534).

The field was a leftover of the allocation feature removed in #264; nothing
reads it, and cost allocation declares its dimension by a Link on the Rule
Set. Frappe never drops a column removed from the DocType JSON, so this does.
A rerun, or a fresh site that never had the column, changes nothing.
"""
import frappe


def execute():
    if frappe.db.has_column("Dimension", "allocation_role"):
        frappe.db.sql_ddl("ALTER TABLE `tabDimension` DROP COLUMN `allocation_role`")

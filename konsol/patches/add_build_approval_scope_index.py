"""Index Build Approval.build_scope (#133 review).

Both build-request debounces take `SELECT ... FROM tabBuild Approval WHERE
build_scope = ... FOR UPDATE`. With no index InnoDB scanned, and locked, every
row, so one request blocked every Build Approval write until it committed. The
DocType JSON now declares search_index on the field, which covers fresh
installs; this adds the same index on sites that already have the table.
Idempotent: add_index checks for the index first.
"""
import frappe


def execute():
    if frappe.db.table_exists("Build Approval"):
        frappe.db.add_index("Build Approval", ["build_scope"])

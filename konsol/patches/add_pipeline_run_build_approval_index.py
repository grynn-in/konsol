"""Index Pipeline Run.build_approval (#140).

The failed-start retry chain asks, per Build Approval, whether any of its
Pipeline Runs shows a build (reaper._ever_built). With no index that scanned
the whole table on every lookup. The DocType JSON now declares search_index on
the field, which covers fresh installs; this adds the same index on sites that
already have the table, as add_build_approval_scope_index did for build_scope.
Idempotent: add_index checks for the index first.
"""
import frappe


def execute():
    if frappe.db.table_exists("Pipeline Run"):
        frappe.db.add_index("Pipeline Run", ["build_approval"])

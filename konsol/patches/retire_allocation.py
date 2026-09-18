"""Retire the deprecated allocation doctypes (konsol#264).

konsol#264 (Deepak Pai, 18 Sep 2026): the cost-allocation feature is REMOVED
entirely — not deprecated in favour of a successor, gone. All four
``epm_staging.allocation_*`` tables are empty on live. The doctype code
(``Allocation Rule`` + its ``Allocation Tier`` child table, ``Allocation
Driver``, ``Allocation Run`` + its dormant per-doc workflow) and the
``konsol/allocation`` package were deleted in rows K2/K3/K6/K7/K8/K9a/K9b of
that removal. This patch is the cutover step those rows left open: delete_doc
on a DocType removes the meta but deliberately leaves the table behind, so an
existing site still carries the four ``tabAllocation *`` tables (and possibly
the workflow doc, if a site ever ran ``install_workflows`` while the
definition was briefly wired up) until this runs.

DEVIATION from ``retire_budget_input``'s precedent: that patch throws when it
finds unmigrated data, because Budget Input had a migration target (Budget
Sheet) and a throw there prevents destroying the only copy of live data.
Allocation has no such target — the feature itself is gone, there is nowhere
for these rows to go — so the equivalent parity gate would instead wedge
`bench migrate` permanently on any site that ever held allocation data, with
no way to satisfy it. Instead, this patch counts the rows in each table
before dropping it and logs the counts (guarded by ``table_exists`` so a
fresh install, which never created the table, no-ops cleanly), leaving an
audit trail without blocking anyone. It does NOT throw.

Idempotent: ``ignore_missing=True`` on every ``delete_doc`` and
``DROP TABLE IF EXISTS`` on every table make a rerun a no-op.
"""
import frappe


def execute():
    # Audit trail only (see module docstring for why this does not throw):
    # log any residual row counts before the tables are dropped.
    for table in (
        "Allocation Tier",
        "Allocation Driver",
        "Allocation Run",
        "Allocation Rule",
    ):
        if frappe.db.table_exists(table):
            count = frappe.db.count(table)
            frappe.logger().info(
                "retire_allocation: tab{0} had {1} row(s) at retirement "
                "(konsol#264: allocation is removed with no migration "
                "target, so these rows are being dropped, not moved)".format(
                    table, count
                )
            )

    frappe.delete_doc(
        "Workflow", "Allocation Run Workflow", force=True, ignore_missing=True
    )

    # Child table before parent: Allocation Tier is a child table of
    # Allocation Rule.
    frappe.delete_doc(
        "DocType", "Allocation Tier", force=True, ignore_missing=True
    )
    frappe.delete_doc(
        "DocType", "Allocation Rule", force=True, ignore_missing=True
    )
    frappe.delete_doc(
        "DocType", "Allocation Driver", force=True, ignore_missing=True
    )
    frappe.delete_doc(
        "DocType", "Allocation Run", force=True, ignore_missing=True
    )

    # delete_doc leaves the data tables in place; drop them explicitly
    # (child table first, matching the delete_doc order above).
    frappe.db.sql_ddl("DROP TABLE IF EXISTS `tabAllocation Tier`")
    frappe.db.sql_ddl("DROP TABLE IF EXISTS `tabAllocation Rule`")
    frappe.db.sql_ddl("DROP TABLE IF EXISTS `tabAllocation Driver`")
    frappe.db.sql_ddl("DROP TABLE IF EXISTS `tabAllocation Run`")

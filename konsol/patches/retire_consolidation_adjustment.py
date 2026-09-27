"""Retire Consolidation Adjustment on existing sites (konsol#305 J13).

#305-D2-1 (Deepak Pai, 27 Sep 2026): the Consolidation Journal replaces
Consolidation Adjustment outright. Row J12 deleted the doctype's code. This
patch is the cut-over for a site that already migrated it.

Frappe's migrate deletes an orphan DocType record
(frappe/model/sync.py ``remove_orphan_doctypes``), but it leaves the
``Consolidation Adjustment Workflow`` record and the ``tabConsolidation
Adjustment`` table behind. ``delete_doc`` on a standard DocType also leaves
the table. So this patch deletes the Workflow, then the DocType, then drops
the table.

Refusal (decided 27 Sep, Problems P14): while the table holds any row, the
patch throws and changes nothing. Unlike ``retire_allocation`` (no successor,
so it only logs), these rows have a successor: the Consolidation Journal. The
Close Lead re-enters them as journals and deletes them, then migrates again.
Live had 0 rows on 27 Sep.

Idempotent: the count is guarded by ``table_exists``, every ``delete_doc``
passes ``ignore_missing=True``, and the drop is ``DROP TABLE IF EXISTS``, so
a fresh install or a rerun is a no-op.
"""
import frappe


def execute():
    if frappe.db.table_exists("Consolidation Adjustment"):
        rows = frappe.db.count("Consolidation Adjustment")
        if rows:
            frappe.throw(
                "{0} Consolidation Adjustment rows exist; re-enter them as "
                "Consolidation Journals and delete them, then migrate".format(rows)
            )

    # The Workflow first: it names the DocType.
    frappe.delete_doc(
        "Workflow",
        "Consolidation Adjustment Workflow",
        force=True,
        ignore_missing=True,
    )
    frappe.delete_doc(
        "DocType", "Consolidation Adjustment", force=True, ignore_missing=True
    )

    # delete_doc leaves the table of a standard DocType; drop it explicitly.
    frappe.db.sql_ddl("DROP TABLE IF EXISTS `tabConsolidation Adjustment`")

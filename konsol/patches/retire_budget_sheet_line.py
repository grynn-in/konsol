"""Retire Budget Sheet and Budget Line (konsol#348 S2-C Z1).

Budget Entry and Budget Entry Line (#522) replace them: budget intake runs on
the trial-balance contract, and nothing writes or reads a Sheet any more. The
doctype code left the app with this patch. Frappe's migrate deletes an orphan
DocType record, but it leaves the tables, the Custom Fields (Budget Line's
in_budget dimension columns) and any Property Setter behind, so this patch
removes them all:

1. Rows still in either table are named in the migrate log, then dropped.
   There is no refusal: Budget Entry is a fresh intake, not a migration of
   Sheet rows, and the stack had no customers when this landed.
2. The Custom Fields and Property Setters on both doctypes are deleted.
3. The DocType records are deleted, parent before its child table.
4. Both tables are dropped.

Idempotent: the counts are guarded by ``table_exists``, ``delete_doc`` passes
``ignore_missing=True``, the deletes filter by doctype and the drops are
``DROP TABLE IF EXISTS``, so a rerun or a fresh install is a no-op.
"""
import frappe

#: Parent first: Budget Sheet's ``lines`` field names Budget Line.
DOCTYPES = ("Budget Sheet", "Budget Line")


def execute():
    for doctype in DOCTYPES:
        if not frappe.db.table_exists(doctype):
            continue
        rows = frappe.db.count(doctype)
        if rows:
            message = (f"konsol#348 S2-C Z1: dropping {rows} {doctype} row(s); "
                       "Budget Entry replaces it")
            print(message)
            frappe.logger().warning(message)

    frappe.db.delete("Custom Field", {"dt": ("in", DOCTYPES)})
    frappe.db.delete("Property Setter", {"doc_type": ("in", DOCTYPES)})

    for doctype in DOCTYPES:
        frappe.delete_doc("DocType", doctype, force=True, ignore_missing=True)

    # delete_doc leaves the table of a standard DocType; drop it explicitly.
    for doctype in DOCTYPES:
        frappe.db.sql_ddl(f"DROP TABLE IF EXISTS `tab{doctype}`")

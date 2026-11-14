"""Retire Budget Sheet and Budget Line (konsol#348 S2-C Z1).

Budget Entry and Budget Entry Line (#522) replace them: budget intake runs on
the trial-balance contract, and nothing writes or reads a Sheet any more. The
doctype code left the app with this patch. Frappe's migrate deletes an orphan
DocType record, but it leaves the tables, the Custom Fields (Budget Line's
in_budget dimension columns) and any Property Setter behind, so this patch
removes them all:

1. Rows still in either table refuse the migrate, naming both counts, and
   nothing is dropped: a drop cannot be undone. The operator exports or
   deletes the rows (Budget Entry is a fresh intake, not a migration of Sheet
   rows), then migrates again. Every known site held 0 rows on 14 Nov 2026.
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


class SheetRowsRemain(Exception):
    """Budget Sheet or Budget Line still holds rows: the retirement would drop them."""


def execute():
    remaining = [(frappe.db.count(doctype), doctype) for doctype in DOCTYPES
                 if frappe.db.table_exists(doctype)]
    remaining = [(rows, doctype) for rows, doctype in remaining if rows]
    if remaining:
        counts = ", ".join(f"{rows} {doctype}" for rows, doctype in remaining)
        raise SheetRowsRemain(
            f"konsol#348 S2-C: {counts} row(s) remain. Budget Sheet and Budget Line are retired "
            "and this patch drops their tables, which cannot be undone. Export or delete the rows "
            "(budgets are now entered as Budget Entries), then run migrate again.")

    frappe.db.delete("Custom Field", {"dt": ("in", DOCTYPES)})
    frappe.db.delete("Property Setter", {"doc_type": ("in", DOCTYPES)})

    for doctype in DOCTYPES:
        frappe.delete_doc("DocType", doctype, force=True, ignore_missing=True)

    # delete_doc leaves the table of a standard DocType; drop it explicitly.
    for doctype in DOCTYPES:
        frappe.db.sql_ddl(f"DROP TABLE IF EXISTS `tab{doctype}`")

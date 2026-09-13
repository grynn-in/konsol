"""Remove EPM Settings.consolidation_currency (konsolidat#93).

The presentation currency lives on each Consolidation Group node (decided 13
Sep 2026); this setting was read by nothing. Frappe removes the field from the
DocType when the JSON changes, but never the stored value. EPM Settings is a
Single, so the value is a row in tabSingles rather than a column: delete it,
and any property setter on the field, so no stale "consolidation currency"
survives to look configured.
"""
import frappe

FIELD = "consolidation_currency"


def execute():
    # Plain SQL: get_value on Singles orders by `modified`, which tabSingles lacks.
    rows = frappe.db.sql(
        "SELECT value FROM `tabSingles` WHERE doctype = %s AND field = %s", ("EPM Settings", FIELD))
    value = rows[0][0] if rows else None
    frappe.db.delete("Singles", {"doctype": "EPM Settings", "field": FIELD})
    frappe.db.delete("Property Setter", {"doc_type": "EPM Settings", "field_name": FIELD})
    if value:
        print(f"konsolidat#93: dropped EPM Settings.{FIELD} = {value!r} (read by nothing; "
              "the presentation currency is each Consolidation Group node's)")

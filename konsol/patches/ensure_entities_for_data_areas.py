"""Create an Entity for every data area still referenced without one.

Runs immediately before `data_area_id` becomes a Link to Entity on six
doctypes. A Link is validated on every save, so any row holding a code with no
matching Entity would become unsaveable — and the failure would surface later,
on an unrelated edit, as "Could not find Entity: XYZ".

backfill_entities_from_consolidation_group covers everything in the
Consolidation Group tree, which on a typical site is all of them. This sweeps
the other five for anything that tree never knew about: an allocation driver
for a disposed entity, a budget sheet from before a restructure, an equity rate
for a company that left the group.

Entities created here are intentionally bare — code and name only, no parent,
no functional currency. They are placeholders that make the data consistent,
not an attempt to guess structure. They land at the root of the tree where they
are visible, rather than being hidden under a plausible-looking parent.
"""

import frappe

#: Every doctype whose data_area_id is about to become a Link.
REFERRING_DOCTYPES = (
    "Consolidation Group",
    "Ownership Period",
    "Budget Sheet",
    "Consolidation Adjustment",
    "Historical Equity Rate",
    "Allocation Driver",
)


def execute():
    # Patches run pre_model_sync, so Entity's table may not exist yet.
    frappe.reload_doc("epm", "doctype", "entity")
    if not frappe.db.table_exists("Entity"):
        return

    known = {e.name for e in frappe.get_all("Entity", limit_page_length=0)}
    created = []

    for doctype in REFERRING_DOCTYPES:
        if not frappe.db.table_exists(doctype):
            continue
        if not frappe.db.has_column(doctype, "data_area_id"):
            continue

        for row in frappe.db.sql(
            f"SELECT DISTINCT data_area_id FROM `tab{doctype}` "
            "WHERE data_area_id IS NOT NULL AND data_area_id != ''",
            as_dict=True,
        ):
            code = (row.data_area_id or "").strip().upper()
            if not code or code in known:
                continue

            doc = frappe.new_doc("Entity")
            doc.entity_code = code
            doc.entity_name = code
            doc.is_group = 0
            doc.status = "Active"
            doc.flags.ignore_permissions = True
            doc.insert()

            known.add(code)
            created.append((code, doctype))

    if created:
        frappe.db.commit()
        for code, doctype in created:
            frappe.logger().info(
                f"konsol: created placeholder Entity {code!r} referenced by {doctype}"
            )

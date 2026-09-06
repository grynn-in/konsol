"""Seed the Entity master from the Consolidation Group tree.

Consolidation Group has been doing two jobs: roll-up nodes (``is_group=1``,
no ``data_area_id``) and the legal entities themselves (``is_group=0``, with a
``data_area_id``). This lifts both into Entity so there is one place an entity
exists, keeping the same shape of tree.

Deliberately additive. Nothing is renamed, no field on any existing doctype
changes type, and Consolidation Group is untouched — converting the six
``data_area_id`` Data fields into Links is a separate step that this one has to
land before.

What is NOT copied: ``reporting_currency``. On a leaf row it holds the *group's*
reporting currency (every Contoso entity reads USD, every Alpine entity CHF),
which is not the same thing as the entity's functional currency — the currency
it keeps its books in, and the one FX translation is computed *from*. Copying it
would put plausible, wrong data in a field the consolidation depends on. It is
left blank for someone who knows the answer to fill in.
"""

import frappe


def execute():
    if not frappe.db.table_exists("Consolidation Group"):
        return

    # konsol's patches.txt has no section headers, so Frappe's old-format
    # fallback runs every patch in the pre_model_sync phase — before the app's
    # DocType JSON is synced. Without this reload the Entity table does not
    # exist yet and the backfill is a silent no-op that still records itself as
    # run, so it never fires again.
    frappe.reload_doc("epm", "doctype", "entity")

    if not frappe.db.table_exists("Entity"):
        return

    rows = frappe.get_all(
        "Consolidation Group",
        fields=[
            "name", "consolidation_group", "data_area_id", "entity_name",
            "parent_consolidation_group", "is_group",
        ],
        order_by="lft asc",
        limit_page_length=0,
    )
    if not rows:
        return

    # Consolidation Group name -> the Entity code it becomes. Group nodes are
    # identified by their group code, leaves by their data area.
    code_of = {}
    for r in rows:
        code = (r.data_area_id or "").strip() if not r.is_group else (r.consolidation_group or "").strip()
        if code:
            code_of[r.name] = code.upper()

    created = 0
    for r in rows:
        code = code_of.get(r.name)
        if not code or frappe.db.exists("Entity", code):
            continue

        parent_code = code_of.get(r.parent_consolidation_group)
        # Parents are created first because the query is ordered by lft, but a
        # tree with a gap (a leaf whose parent had no code) would otherwise
        # dangle — attach those at the root instead of failing the migrate.
        if parent_code and not frappe.db.exists("Entity", parent_code):
            parent_code = None

        doc = frappe.new_doc("Entity")
        doc.entity_code = code
        doc.entity_name = (r.entity_name or code).strip()
        doc.is_group = 1 if r.is_group else 0
        doc.parent_entity = parent_code
        doc.status = "Active"
        doc.flags.ignore_permissions = True
        doc.insert()
        created += 1

    if created:
        frappe.db.commit()
        frappe.logger().info(f"konsol: backfilled {created} Entity records from Consolidation Group")

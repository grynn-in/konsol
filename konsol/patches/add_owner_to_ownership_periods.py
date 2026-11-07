"""Every existing Ownership Period gets its owner node (konsol#117 pair 1, design §9).

Decided by Deepak Pai on 7 Nov 2026 (option A): an Ownership Period is a link
of the ownership graph, from an owner node to the owned node. Task A gave the
doctype ``owner_node`` (pre-filled with the owned node's tree parent when left
blank, the one default of the design) and the derived ``owner_group``,
``owner_data_area_id`` and ``owner_label``. Every period written before that
has the four blank; on the live stack that is 329 submitted rows.

Each gets exactly what the controller would pre-fill: ``owner_node`` is the
owned node's ``parent_consolidation_group`` (the Consolidation Group row named
by the period's ``(consolidation_group, data_area_id)``), and the three derived
columns come from the controller's own ``owner_fields``, so the patch and a
save cannot drift. For a tree this is the only possible owner, so no live
number changes (§9: 329 periods → 329 links, one path per entity).

Done at the DB level (``frappe.db.set_value``), not via the doc lifecycle:
the doctype is submittable, ``save()`` rejects a change to a submitted row, and
the on_submit sync must not run per row here. Names are NOT changed (§1:
existing names stay; only a new period is named after its owner).
after_migrate's ``reconcile_all`` re-sends the table with the owner columns.

A period whose owned node does not exist, is a root (nobody owns the top; the
controller refuses such a period, so this cannot happen on a validated site),
or whose tree parent is not a node stops the migrate naming the period:
guessing an owner would invent a link. Nothing is written when any row fails.
Idempotent: a second run finds every ``owner_node`` filled and writes nothing.

Runs before the model sync (konsol's patches.txt has no sections), so it
reloads the doctype first to have the columns.
"""
import frappe

from konsol.consolidation.doctype.ownership_period.ownership_period import owner_fields

DOCTYPE = "Ownership Period"
OWNER_COLUMNS = ("owner_node", "owner_group", "owner_data_area_id", "owner_label")


def _key(group, entity):
    return (group, entity or None)


def plan(rows, nodes):
    """The writes to make and the periods that cannot be filled. Pure, so
    the mapping is testable without a site.

    ``rows``: the periods as frappe.get_all returns them (dicts with name,
    consolidation_group, data_area_id, owner_node), of any docstatus.
    ``nodes``: the Consolidation Group rows (name, consolidation_group,
    data_area_id, parent_consolidation_group).

    Returns ``([(name, {owner_node, owner_group, owner_data_area_id,
    owner_label})], [(name, reason)])``. A period whose owner is already set
    is left alone, which is what makes a second run write nothing.
    """
    by_key = {_key(n.get("consolidation_group"), n.get("data_area_id")): n for n in nodes}
    by_name = {n.get("name"): n for n in nodes}
    updates, problems = [], []
    for row in rows:
        if row.get("owner_node"):
            continue
        name = row.get("name")
        group, entity = row.get("consolidation_group"), row.get("data_area_id") or None
        owned = by_key.get(_key(group, entity))
        if owned is None:
            where = f"entity '{entity}'" if entity else "the group node"
            problems.append((name, f"no Consolidation Group node '{group}' / {where}"))
            continue
        parent_name = owned.get("parent_consolidation_group")
        if not parent_name:
            problems.append((name, f"its node {owned.get('name')} is a root: nobody owns the top of a hierarchy"))
            continue
        owner = by_name.get(parent_name)
        if owner is None:
            problems.append((name, f"its node {owned.get('name')} names parent {parent_name}, "
                                   "which is not a Consolidation Group node"))
            continue
        values = {"owner_node": owner.get("name")}
        values.update(owner_fields(owner))
        updates.append((name, values))
    return updates, problems


def execute():
    if not frappe.db.table_exists(DOCTYPE):
        return
    frappe.reload_doc("consolidation", "doctype", "ownership_period")
    rows = frappe.get_all(
        DOCTYPE,
        fields=["name", "consolidation_group", "data_area_id", "owner_node"],
        limit_page_length=0,
    )
    nodes = frappe.get_all(
        "Consolidation Group",
        fields=["name", "consolidation_group", "data_area_id", "parent_consolidation_group"],
        limit_page_length=0,
    )
    updates, problems = plan(rows, nodes)
    if problems:
        detail = "; ".join(f"{name}: {reason}" for name, reason in sorted(problems))
        frappe.throw(
            "Cannot fill Owner Node on these Ownership Periods, and will not guess "
            f"one: {detail}. Create the node, or cancel and delete the period, then "
            "migrate again.",
            frappe.ValidationError,
        )
    for name, values in updates:
        frappe.db.set_value(DOCTYPE, name, values, update_modified=False)

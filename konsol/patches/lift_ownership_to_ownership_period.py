"""Lift ownership off Consolidation Group into Ownership Period (F2).

Consolidation Group carried ``ownership_pct`` and ``consolidation_method``
alongside Ownership Period, which carries the same two at a *dated* grain. Two
grains, no rule about which won, and the dbt side invented one that read a real
0% as "unset". F2 makes Ownership Period the only place ownership lives, so this
copies what the tree holds today into an open-ended period per node before those
two fields disappear.

Idempotent, and deliberately conservative:

* nothing is created for a node that already has a period covering the floor
  date — a hand-entered period always wins over a lifted default;
* nothing is created for a ROOT node: nobody owns the top of a hierarchy, it is
  100% by construction, and OwnershipPeriod refuses such a period;
* whatever the tree says is carried over verbatim, including a 0. Frappe emits
  a Float as `decimal(21,9) not null default 0`, so a node that never had a
  percentage is indistinguishable from one deliberately set to 0 — and
  `ownership_pct` was `reqd: 1`, so a genuine unset could not be entered
  through the desk anyway. Inventing a 100 for the ambiguous case is
  `ownership_pct or 100`, the falsy-zero bug this work removes, one layer down.

Once every non-root node HAS a period — checked afterwards, not assumed — the
two columns are DROPPED from
`tabConsolidation Group`. Frappe does not remove a column when a field leaves a
DocType JSON, so without this the old percentages sit in MariaDB forever — the
second source of truth F2 exists to delete, still readable by any `frappe.db.sql`
— and an upgraded site would differ from a fresh one. The drop is deliberately
conditional: if any node could not be lifted, the column stays so the data is
recoverable.

The floor date is deliberately far back: the lifted period is the standing
ownership "as it has always been", and any real acquisition date is recorded by
adding a later period, not by editing this one. It is 1970-01-01 because that is
the earliest date ClickHouse's Date type can hold — 1900-01-01 was silently
clamped to 1970-01-01 on sync, so the two stores disagreed about what the
document said.
"""

import frappe

# The earliest value a ClickHouse Date column can represent.
FLOOR_DATE = "1970-01-01"


def execute():
    if not frappe.db.table_exists("Consolidation Group"):
        return

    # konsol's patches.txt has no section headers, so Frappe's old-format
    # fallback runs every patch in pre_model_sync — BEFORE the DocType JSON is
    # synced. That is what makes this patch possible at all: the old
    # ownership_pct column still exists here. Reload Ownership Period so the
    # inserts below see its current shape (F2 makes data_area_id optional).
    frappe.reload_doc("consolidation", "doctype", "ownership_period")

    if not frappe.db.has_column("Consolidation Group", "ownership_pct"):
        return  # already lifted, or a fresh install that never had the column

    # Raw SQL, not frappe.get_all: the controller no longer declares these two
    # fields, so the ORM would not return them.
    nodes = frappe.db.sql(
        """
        select name, consolidation_group, data_area_id,
               parent_consolidation_group, ownership_pct, consolidation_method
        from `tabConsolidation Group`
        """,
        as_dict=True,
    )

    created = 0
    for node in nodes:
        if not node.parent_consolidation_group:
            continue  # root: 100% by construction
        if _has_period(node):
            continue
        doc = frappe.get_doc({
            "doctype": "Ownership Period",
            "consolidation_group": node.consolidation_group,
            "data_area_id": node.data_area_id or None,
            "effective_date": FLOOR_DATE,
            "ownership_pct": float(node.ownership_pct),
            "consolidation_method": node.consolidation_method or "full",
        })
        doc.flags.ignore_permissions = True
        doc.insert()
        doc.submit()
        created += 1

    frappe.logger().info(
        f"F2: lifted {created} ownership period(s) from Consolidation Group"
    )

    # Post-condition, not a guess: EVERY non-root node must now have a period.
    # A node can be missing one because its insert raised (an overlap, a date
    # outside ClickHouse's range, a node that validate refused) — all of which
    # leave the lift incomplete without raising here.
    unlifted = [n.name for n in nodes
                if n.parent_consolidation_group and not _has_period(n)]
    if unlifted:
        frappe.logger().warning(
            "F2: these Consolidation Group nodes have NO Ownership Period, so "
            "nothing below them consolidates — add one for each, then re-run "
            "this patch. Their old ownership columns are left in place so the "
            f"figures are recoverable: {', '.join(unlifted)}"
        )
        return

    _drop_lifted_columns()


def _drop_lifted_columns():
    """Remove the retired columns from `tabConsolidation Group`.

    Frappe adds and alters columns on migrate but never drops one whose field has
    left the DocType JSON, so ownership_pct and consolidation_method would keep
    their pre-F2 values in MariaDB indefinitely: invisible to the ORM, readable
    by raw SQL, and different from what a fresh install has. Only reached once
    every non-root node has a period.
    """
    for column in ("ownership_pct", "consolidation_method"):
        if not frappe.db.has_column("Consolidation Group", column):
            continue
        try:
            frappe.db.sql_ddl(
                f"alter table `tabConsolidation Group` drop column `{column}`")
            frappe.logger().info(f"F2: dropped Consolidation Group.{column}")
        except Exception:  # noqa: BLE001 — a failed drop must not fail a migrate
            frappe.logger().warning(
                f"F2: could not drop Consolidation Group.{column}; it is unused "
                f"but still present", exc_info=True)


def _has_period(node):
    """True when a live period already covers this node at the floor date."""
    blank = ["is", "not set"]
    existing = frappe.get_all(
        "Ownership Period",
        filters={
            "consolidation_group": node.consolidation_group,
            # A blank Link is stored as NULL, so {"data_area_id": ""} matches
            # nothing — the trap that made konsol #112's uniqueness guard inert.
            "data_area_id": node.data_area_id or blank,
            "docstatus": ["!=", 2],
        },
        fields=["effective_date", "end_date"],
        limit_page_length=0,
    )
    floor = frappe.utils.getdate(FLOOR_DATE)
    return any(
        frappe.utils.getdate(p.effective_date) <= floor
        and frappe.utils.getdate(p.end_date or "9999-12-31") >= floor
        for p in existing
    )

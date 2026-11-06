"""Move budget permission grants from Budget Cost Center to Dimension Value.

konsol#287 decision 2A (Deepak Pai, 6 Nov 2026): the generic, read-only
``Dimension Value`` (named ``<dimension>:<value>``) replaces the virtual
doctype Budget Cost Center, which hard-coded one customer's dimension.

What this does, decided by ``dimension_value_model.plan_retirement``:

1. Every Dimension whose ``permission_doctype`` is Budget Cost Center is
   pointed at Dimension Value.
2. Every User Permission on Budget Cost Center is rewritten to Dimension
   Value, its ``for_value`` prefixed with the dimension the retired doctype
   held: the one Dimension that pointed at it. A grant that already
   exists on Dimension Value makes the old row redundant, and it is deleted.
3. A row it cannot map (no single dimension, or a blank value) is REPORTED,
   printed and written to the Error Log, and left as it is. It is never
   guessed onto a dimension. No Dimension names Budget Cost Center after
   step 1, so nothing reads a left row and it grants nothing; Frappe's
   ``get_user_permissions`` tolerates its missing DocType. The report says
   which rows an admin must re-grant on Dimension Value.
4. The Budget Cost Center DocType record is deleted (virtual: no table).

Runs before the model sync (konsol's patches.txt has no sections), so it uses
direct writes: Dimension Value need not exist yet, and nothing here validates
a link. Idempotent: a second run finds nothing on Budget Cost Center.
"""
import frappe

from konsol import dimension_value_model as dvm


def execute():
    retired = dvm.RETIRED_DOCTYPE
    dimensions = frappe.get_all(
        "Dimension",
        fields=["name", "permission_doctype"],
        order_by="name asc",
        limit_page_length=0,
    ) if frappe.db.table_exists("Dimension") else []
    grants = frappe.get_all(
        "User Permission",
        filters={"allow": retired},
        fields=["name", "user", "for_value", "applicable_for"],
        order_by="name asc",
        limit_page_length=0,
    )
    existing = frappe.get_all(
        "User Permission",
        filters={"allow": dvm.DOCTYPE},
        fields=["user", "for_value", "applicable_for"],
        limit_page_length=0,
    )
    plan = dvm.plan_retirement(dimensions, grants, existing)

    for name in plan["repoint"]:
        frappe.db.set_value("Dimension", name, "permission_doctype", dvm.DOCTYPE,
                            update_modified=False)
        print(f"adopt_dimension_value: Dimension {name} now gated by {dvm.DOCTYPE}")
    for name, new_value in plan["rewrite"]:
        frappe.db.set_value("User Permission", name,
                            {"allow": dvm.DOCTYPE, "for_value": new_value},
                            update_modified=False)
        print(f"adopt_dimension_value: User Permission {name} -> {dvm.DOCTYPE} {new_value}")
    for name, new_value in plan["duplicate"]:
        frappe.db.delete("User Permission", {"name": name})
        print(f"adopt_dimension_value: User Permission {name} deleted; "
              f"{dvm.DOCTYPE} {new_value} was already granted")
    if plan["unmapped"]:
        lines = [f"{name}: {reason}" for name, reason in plan["unmapped"]]
        message = (
            f"{len(lines)} User Permission(s) on {retired} could not be moved to "
            f"{dvm.DOCTYPE} and were left as they are; they grant nothing now. "
            f"Re-grant each on {dvm.DOCTYPE} as <dimension>:<value>.\n"
            + "\n".join(lines)
        )
        print(f"adopt_dimension_value: {message}")
        frappe.log_error("adopt_dimension_value: unmapped User Permissions", message)

    if plan["rewrite"] or plan["duplicate"]:
        frappe.cache.delete_value("user_permissions")

    frappe.delete_doc("DocType", retired, force=True, ignore_missing=True,
                      ignore_permissions=True)

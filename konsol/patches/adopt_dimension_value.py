"""Move budget permission grants from Budget Cost Center to Dimension Value.

konsol#287 decision 2A (Deepak Pai, 6 Nov 2026): the generic, read-only
``Dimension Value`` (named ``<dimension>:<value>``) replaces the virtual
doctype Budget Cost Center, which hard-coded one customer's dimension.

What this does, decided by ``dimension_value_model.plan_retirement``:

1. Every Dimension whose ``permission_doctype`` is Budget Cost Center is
   pointed at Dimension Value.
2. Every User Permission on Budget Cost Center becomes ``<d>:<value>`` on
   Dimension Value for EVERY such Dimension ``d``: the old gate compared a
   grant with each of them, so it allowed the value on all of them. The old
   row is rewritten to the first; copies are inserted for the rest. A grant
   that already exists on Dimension Value is not repeated, and an old row
   left with nothing new is deleted.
3. A row it cannot map (no Dimension pointed at Budget Cost Center, so none
   ever enforced it, or a blank value) is never guessed onto a dimension. It
   is REPORTED in full (user, allow, for_value, applicable_for,
   apply_to_all_doctypes, is_default), printed and written to the Error Log,
   and then deleted, so no User Permission names a DocType that no longer
   exists. The report is what an admin re-grants from.
4. The Budget Cost Center DocType record is deleted (virtual: no table).

Runs before the model sync (konsol's patches.txt has no sections), so it uses
direct writes: Dimension Value need not exist yet, and nothing here validates
a link. Idempotent: a second run finds nothing on Budget Cost Center.
"""
import json

import frappe

from konsol import dimension_value_model as dvm

#: Every field of a User Permission that says what it grants.
_GRANT_FIELDS = ["name", "user", "allow", "for_value", "applicable_for",
                 "apply_to_all_doctypes", "is_default", "hide_descendants"]


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
        fields=_GRANT_FIELDS,
        order_by="name asc",
        limit_page_length=0,
    )
    by_name = {g.name: g for g in grants}
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

    # Copies first, while the source row still reads as it was.
    for name, new_value in plan["add"]:
        source = by_name[name]
        copy = frappe.get_doc({
            "doctype": "User Permission",
            "user": source.user,
            "allow": dvm.DOCTYPE,
            "for_value": new_value,
            "applicable_for": source.applicable_for,
            "apply_to_all_doctypes": source.apply_to_all_doctypes,
            "is_default": source.is_default,
            "hide_descendants": source.hide_descendants,
        })
        # ignore_links: Dimension Value may not be synced yet (pre-model-sync).
        copy.insert(ignore_permissions=True, ignore_links=True)
        print(f"adopt_dimension_value: User Permission {copy.name} added for "
              f"{source.user}: {dvm.DOCTYPE} {new_value} (from {name})")
    for name, new_value in plan["rewrite"]:
        frappe.db.set_value("User Permission", name,
                            {"allow": dvm.DOCTYPE, "for_value": new_value},
                            update_modified=False)
        print(f"adopt_dimension_value: User Permission {name} -> {dvm.DOCTYPE} {new_value}")
    for name, new_value in plan["duplicate"]:
        print(f"adopt_dimension_value: {dvm.DOCTYPE} {new_value} was already granted "
              f"to {by_name[name].user}; not repeated from {name}")

    if plan["unmapped"]:
        records = [
            {"reason": reason,
             **{f: by_name[name].get(f) for f in (
                 "name", "user", "allow", "for_value", "applicable_for",
                 "apply_to_all_doctypes", "is_default")}}
            for name, reason in plan["unmapped"]
        ]
        message = (
            f"{len(records)} User Permission(s) on {retired} could not be moved to "
            f"{dvm.DOCTYPE} and were deleted. Each is recorded below in full; "
            f"re-grant it on {dvm.DOCTYPE} as <dimension>:<value> if it is still "
            f"wanted.\n" + json.dumps(records, indent=1, default=str)
        )
        print(f"adopt_dimension_value: {message}")
        frappe.log_error("adopt_dimension_value: unmapped User Permissions", message)

    for name in plan["remove"]:
        frappe.db.delete("User Permission", {"name": name})
        print(f"adopt_dimension_value: User Permission {name} on {retired} deleted")

    if grants:
        frappe.cache.delete_value("user_permissions")

    frappe.delete_doc("DocType", retired, force=True, ignore_missing=True,
                      ignore_permissions=True)

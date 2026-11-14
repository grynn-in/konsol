"""Retire the Budget* roles from konsol (budget S2-C, Z7, decision Q7-c on #348).

konsol no longer ships Budget Submitter, Budget Controller, Budget Manager or
Budget Approver. On an existing site:

- every holder of one gains Entity Accountant, so they can still open the
  budget doctypes, and keeps the old role;
- a Role record that has holders stays as site data: a Budget Layer may use it
  as its writer role (seed_budget_layers sets those, and runs first);
- a Role with no holders is deleted, unless a Budget Layer names it as
  writer_role or a Custom DocPerm grants it (both are site choices);
- the holders of Budget Manager and Budget Approver are named on the migrate
  log. None of them becomes EPM Admin: the site decides who signs off.

A rerun changes nothing.
"""
import frappe

BUDGET_ROLES = ("Budget Submitter", "Budget Controller", "Budget Manager", "Budget Approver")
#: Holders of these are listed by name: they approved budgets before, and
#: approval is now EPM Admin's (BUD-9-3), which the patch does not grant.
LISTED = ("Budget Manager", "Budget Approver")
NEW_ROLE = "Entity Accountant"


def _holders(role):
    return sorted(set(frappe.get_all(
        "Has Role", filters={"role": role, "parenttype": "User"}, pluck="parent")))


def _site_uses(role):
    """The site names the role itself: a layer's writer role or a custom permission."""
    return bool(frappe.db.exists("Budget Layer", {"writer_role": role})
                or frappe.db.exists("Custom DocPerm", {"role": role}))


def execute():
    # patches.txt has no sections, so this runs before the doctype sync.
    frappe.reload_doc("epm", "doctype", "budget_layer")
    if not frappe.db.exists("Role", NEW_ROLE):
        frappe.get_doc({"doctype": "Role", "role_name": NEW_ROLE, "desk_access": 1}).insert(
            ignore_permissions=True)

    for role in BUDGET_ROLES:
        if not frappe.db.exists("Role", role):
            continue
        holders = _holders(role)
        granted = []
        for user in holders:
            if not frappe.db.exists("Has Role", {"parent": user, "parenttype": "User", "role": NEW_ROLE}):
                frappe.get_doc("User", user).add_roles(NEW_ROLE)
                granted.append(user)
        if granted:
            print(f"retire_budget_roles: {len(granted)} holder(s) of {role} now also hold {NEW_ROLE}")
        if role in LISTED and holders:
            print(f"retire_budget_roles: {role} is held by {', '.join(holders)}. None of them was "
                  f"made EPM Admin; grant it by hand to anyone who should approve budget cycles.")
        if holders:
            continue
        if _site_uses(role):
            print(f"retire_budget_roles: kept {role}: no holder, but a Budget Layer or Custom DocPerm names it")
            continue
        # force: the shipped DocPerm rows that still name the role are dropped
        # by the doctype sync that follows the patches, so they must not block it.
        frappe.delete_doc("Role", role, ignore_permissions=True, force=True)
        print(f"retire_budget_roles: deleted {role}: no holder and nothing on the site names it")

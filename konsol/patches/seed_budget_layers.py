"""Seed the four budget layers on an existing site (budget S2, L2, Q8-a).

Frappe marks every patch done on a fresh install without running it, so a
fresh site stays empty on purpose. An existing site gets base, challenge,
management and board. A writer role is set only where that Role exists and has
at least one holder; otherwise it stays blank. Idempotent: a layer that exists
is never touched.
"""
import frappe

# (layer_code, label, sort_order, writer_role)
LAYERS = (
    ("base", "Base", 1, None),
    ("challenge", "Challenge", 2, "Budget Controller"),
    ("management", "Management", 3, "Budget Manager"),
    ("board", "Board", 4, "Budget Approver"),
)


def _role_is_held(role):
    if not role or not frappe.db.exists("Role", role):
        return False
    return bool(frappe.db.count("Has Role", {"role": role, "parenttype": "User"}))


def execute():
    for code, label, order, role in LAYERS:
        if frappe.db.exists("Budget Layer", code):
            continue
        frappe.get_doc({
            "doctype": "Budget Layer",
            "layer_code": code,
            "label": label,
            "sort_order": order,
            "writer_role": role if _role_is_held(role) else None,
        }).insert(ignore_permissions=True)

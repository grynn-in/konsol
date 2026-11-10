"""Custom HTML Blocks the app ships (konsol#407).

Frappe's Custom HTML Block has no module and no ``is_standard``, so it cannot
ship as a module file the way a Workspace or Number Card does, and migrate
never re-imports one. The app keeps each block's HTML, script and style under
``konsol/consolidation/custom_html_block/<slug>/`` and ``install_custom_blocks``
writes them to the site from ``after_install`` and ``after_migrate``.

The blocks are product-owned (the Handbook decision on konsol#407, option C,
Deepak Pai, 7 Nov 2026: shipped product content updates on deploy): a block
named here is created when missing and overwritten when its stored HTML,
script, style or roles differ from the app's. A site that wants its own block
gives it another name. Nothing else is touched.
"""
import os

import frappe

_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "consolidation", "custom_html_block")

#: (block name, folder, roles that see it). The roles are the ones the
#: block's method admits (overview_api.get_checklist; for the Handbook,
#: handbook.get_handbook, handbook_model.READER_ROLES), so Frappe hides the
#: block from anyone else (desktop.py is_custom_block_permitted).
BLOCKS = (
    ("Konsol Close Checklist", "konsol_close_checklist",
     ("EPM Admin", "EPM Analyst", "EPM User", "System Manager")),
    ("Konsol Handbook", "konsol_handbook",
     ("EPM Admin", "EPM Analyst", "EPM User", "Entity Accountant", "System Manager")),
    # konsol#419: the roles mywork_api.get_my_work admits (frappe.only_for).
    ("Konsol My Work Board", "konsol_my_work_board",
     ("EPM Admin", "EPM Analyst", "Entity Accountant", "EPM User", "System Manager")),
    # konsol#497: the home tabs admit the checklist's roles and Entity
    # Accountant, who sees the Konsol workspace (review R1). The block reads
    # frappe.user_roles and skips the checklist for a user get_checklist
    # refuses; cards, shortcuts and charts come permission-filtered from
    # get_desktop_page, and get_my_work admits all five roles.
    ("Konsol Home Tabs", "konsol_home_tabs",
     ("EPM Admin", "EPM Analyst", "EPM User", "Entity Accountant", "System Manager")),
)


def _read(folder, ext):
    with open(os.path.join(_DIR, folder, "%s.%s" % (folder, ext))) as f:
        return f.read()


def install_custom_blocks():
    """Create or update every block in ``BLOCKS``; return one line per block."""
    lines = []
    for name, folder, roles in BLOCKS:
        want = {"html": _read(folder, "html"), "script": _read(folder, "js"),
                "style": _read(folder, "css"), "private": 0}
        if frappe.db.exists("Custom HTML Block", name):
            doc = frappe.get_doc("Custom HTML Block", name)
            have = {"html": doc.html or "", "script": doc.script or "",
                    "style": doc.style or "", "private": doc.private or 0}
            if have == want and sorted(r.role for r in doc.roles) == sorted(roles):
                lines.append("%s: current" % name)
                continue
            action = "updated"
        else:
            doc = frappe.new_doc("Custom HTML Block")
            doc.name = name
            action = "created"
        doc.update(want)
        doc.set("roles", [{"role": r} for r in roles])
        doc.save() if action == "updated" else doc.insert(set_name=name)
        lines.append("%s: %s" % (name, action))
    return lines

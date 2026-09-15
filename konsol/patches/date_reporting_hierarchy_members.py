"""Date every Reporting Hierarchy Member (konsol#220).

A member is now one dated tranche of its code: effective_from is required,
effective_to blank means it still applies. A member saved before that has no
effective_from. An undated member meant "always", so it gets its hierarchy's
effective_from, or 1900-01-01 when the hierarchy has none. effective_to
stays blank. Members that already have an effective_from are left alone, so a
second run changes nothing.

patches.txt has no sections, so this runs pre_model_sync: it reloads both
doctypes before any query so the new columns exist. Values are written with
db.set_value (no validation, modified untouched).
"""
import frappe

#: effective_from for an undated member under an undated hierarchy.
ALWAYS = "1900-01-01"


def execute():
    frappe.reload_doc("epm", "doctype", "reporting_hierarchy")
    frappe.reload_doc("epm", "doctype", "reporting_hierarchy_member")

    header_from = {
        h["name"]: h.get("effective_from")
        for h in frappe.get_all("Reporting Hierarchy", fields=["name", "effective_from"])
    }

    dated = 0
    for m in frappe.get_all("Reporting Hierarchy Member",
                            fields=["name", "reporting_hierarchy", "effective_from"]):
        if m.get("effective_from"):
            continue
        value = header_from.get(m.get("reporting_hierarchy")) or ALWAYS
        frappe.db.set_value("Reporting Hierarchy Member", m["name"], "effective_from", value,
                            update_modified=False)
        dated += 1

    print("date_reporting_hierarchy_members: %d member(s) dated" % dated)

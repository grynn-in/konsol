"""Date every Reporting Hierarchy Member (konsol#220).

A member is now one dated tranche of its code: effective_from is required,
effective_to blank means it still applies. A member saved before that has no
effective_from. An undated member meant "always", so it gets 1900-01-01
whatever its hierarchy header says: the warehouse never used the header's
dates, and taking them would drop every earlier period from the member's
node. effective_to stays blank. Members that already have an effective_from
are left alone, so a second run changes nothing.

Reads used to refuse a duplicate member_code, so two undated members of one
code could exist; dating both from 1900 makes them overlap and the warehouse
would count them twice. For every (hierarchy, member_code) whose members'
windows overlap after dating, one WARNING line is printed naming them; the
patch does not pick a winner.

patches.txt has no sections, so this runs pre_model_sync: it reloads both
doctypes before any query so the new columns exist. Values are written with
db.set_value (no validation, modified untouched).
"""
import frappe

#: effective_from for an undated member: "always".
ALWAYS = "1900-01-01"
#: open end for the overlap check (the warehouse's open end).
OPEN_END = "2299-12-31"


def _window(m):
    start = str(m.get("effective_from") or ALWAYS)
    end = str(m.get("effective_to") or OPEN_END)
    return start, end


def _overlapping(rows):
    """Names of rows whose window overlaps another row's, sorted."""
    hit = set()
    for i, a in enumerate(rows):
        a_from, a_to = _window(a)
        for b in rows[i + 1:]:
            b_from, b_to = _window(b)
            if a_from <= b_to and b_from <= a_to:
                hit.update((a["name"], b["name"]))
    return sorted(hit)


def execute():
    frappe.reload_doc("epm", "doctype", "reporting_hierarchy")
    frappe.reload_doc("epm", "doctype", "reporting_hierarchy_member")

    members = frappe.get_all("Reporting Hierarchy Member",
                             fields=["name", "reporting_hierarchy", "member_code",
                                     "effective_from", "effective_to"])

    dated = 0
    for m in members:
        if m.get("effective_from"):
            continue
        frappe.db.set_value("Reporting Hierarchy Member", m["name"], "effective_from", ALWAYS,
                            update_modified=False)
        m["effective_from"] = ALWAYS
        dated += 1

    print("date_reporting_hierarchy_members: %d member(s) dated" % dated)

    by_code = {}
    for m in members:
        key = (m.get("reporting_hierarchy") or "", m.get("member_code") or "")
        by_code.setdefault(key, []).append(m)
    for (hierarchy, code), rows in sorted(by_code.items()):
        if len(rows) < 2:
            continue
        names = _overlapping(rows)
        if names:
            print("date_reporting_hierarchy_members: WARNING %s code %s has overlapping rows %s; "
                  "end one before the other starts" % (hierarchy, code, ", ".join(names)))

"""Consolidation Group.reporting_currency becomes a Link to ISO Currency (konsolidat#93).

It was free text with a USD default. A Link rejects a value that is not an ISO
Currency on the next save, so existing values are mapped first:

* a value that is an ISO 4217 code once trimmed and upper-cased is rewritten to
  the code (`usd ` -> `USD`), which is what Frappe's own Link case-correction
  would do on the next save anyway;
* a blank value (on an entity row, where it is not read) is left alone;
* anything else (`US$`, `Dollar`, `EURO`) stops the migrate and names every
  node. Guessing which currency someone meant would silently change what a
  group is translated into.

The codes are ISO Currency's records plus konsol's fixture, which holds the
same list: on a site where the fixture has not yet been imported (patches run
before fixtures), the fixture is the list the Link will validate against.
"""
import json

import frappe


def iso_codes():
    codes = set()
    if frappe.db.table_exists("ISO Currency"):
        codes.update(frappe.get_all("ISO Currency", pluck="name"))
    with open(frappe.get_app_path("konsol", "fixtures", "iso_currency.json")) as f:
        codes.update(row["name"] for row in json.load(f) if row.get("name"))
    return codes


def plan(rows, codes):
    """Split ``rows`` ([(name, value)]) into the rewrites to make and the values
    that don't map. Pure, so the mapping is testable without a site.

    Returns ({name: code}, {value: [names]})."""
    updates, unmapped = {}, {}
    for name, value in rows:
        if value is None or not str(value).strip():
            continue
        code = str(value).strip().upper()
        if code not in codes:
            unmapped.setdefault(value, []).append(name)
        elif code != value:
            updates[name] = code
    return updates, unmapped


def execute():
    if not frappe.db.table_exists("Consolidation Group"):
        return
    rows = frappe.db.sql("SELECT name, reporting_currency FROM `tabConsolidation Group`")
    updates, unmapped = plan(rows, iso_codes())
    if unmapped:
        detail = "; ".join(f"{value!r} on {', '.join(sorted(names))}"
                           for value, names in sorted(unmapped.items(), key=lambda kv: str(kv[0])))
        raise ValueError(
            "Consolidation Group.reporting_currency is becoming a Link to ISO Currency "
            f"(konsolidat#93), and these values are not in ISO Currency: {detail}. "
            "For a real ISO 4217 code missing from the list (it ships 66), add the ISO "
            "Currency record; otherwise set the node's Reporting Currency to the code it "
            "means. Then run the migrate again. Nothing was changed."
        )
    for name, code in sorted(updates.items()):
        frappe.db.set_value("Consolidation Group", name, "reporting_currency", code,
                            update_modified=False)
        print(f"konsolidat#93: Consolidation Group {name}: reporting_currency -> {code}")
    blank_groups = frappe.db.sql(
        "SELECT name FROM `tabConsolidation Group` WHERE (is_group = 1 OR IFNULL(data_area_id, '') = '') "
        "AND IFNULL(TRIM(reporting_currency), '') = ''")
    for (name,) in blank_groups:
        print(f"konsolidat#93: WARNING: group node {name} has no reporting currency; "
              "it must be set before the node can be saved again")

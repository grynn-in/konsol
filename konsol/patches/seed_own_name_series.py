"""Seed each upload doctype's own name series above its existing names (konsol#535).

Trial Balance Upload (`format:TBU-{#####}`) and Budget Upload
(`format:BUU-{#####}`) named from a counter keyed on the empty string, shared
with every other bare-hash format, so their numbers skipped. They now name
`TBU-.#####` and `BUU-.#####`, which key the counter on the prefix. Sites
already hold TBU-/BUU- names, so each new series must start at the highest
existing number or the next insert collides.

Raises `tabSeries` for each prefix to the highest numeric suffix and never
lowers it, so a second run is a no-op.
"""
import re

import frappe

#: doctype -> its name prefix
SERIES = {
    "Trial Balance Upload": "TBU-",
    "Budget Upload": "BUU-",
}


def execute():
    for doctype, prefix in SERIES.items():
        _seed(doctype, prefix)


def _seed(doctype, prefix):
    if not frappe.db.table_exists(doctype):
        return
    rows = frappe.db.sql(f"select name from `tab{doctype}` where name like %s", (prefix + "%",))
    highest = 0
    for (name,) in rows:
        m = re.fullmatch(re.escape(prefix) + r"(\d+)", name or "")
        if m:
            highest = max(highest, int(m.group(1)))
    if not highest:
        return
    cur = frappe.db.sql("select `current` from `tabSeries` where name=%s", (prefix,))
    if not cur:
        frappe.db.sql("insert into `tabSeries` (`name`, `current`) values (%s, %s)", (prefix, highest))
    elif (cur[0][0] or 0) < highest:
        frappe.db.sql("update `tabSeries` set `current`=%s where name=%s", (highest, prefix))

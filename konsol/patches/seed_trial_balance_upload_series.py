"""Seed the `TBU-` series above existing Trial Balance Upload names (konsol#535).

Trial Balance Upload moved from `format:TBU-{#####}` (counter keyed on the empty
string, shared with every other bare-hash format) to `TBU-.#####`, which keys
the counter on `TBU-`. Sites already hold TBU-00001..n, so the new series must
start at the highest existing number or the next insert collides.

Raises `tabSeries` for `TBU-` to the highest numeric suffix and never lowers
it, so a second run is a no-op.
"""
import re

import frappe

DOCTYPE = "Trial Balance Upload"
PREFIX = "TBU-"


def execute():
    if not frappe.db.table_exists(DOCTYPE):
        return
    rows = frappe.db.sql(f"select name from `tab{DOCTYPE}` where name like %s", (PREFIX + "%",))
    highest = 0
    for (name,) in rows:
        m = re.fullmatch(re.escape(PREFIX) + r"(\d+)", name or "")
        if m:
            highest = max(highest, int(m.group(1)))
    if not highest:
        return
    cur = frappe.db.sql("select `current` from `tabSeries` where name=%s", (PREFIX,))
    if not cur:
        frappe.db.sql("insert into `tabSeries` (`name`, `current`) values (%s, %s)", (PREFIX, highest))
    elif (cur[0][0] or 0) < highest:
        frappe.db.sql("update `tabSeries` set `current`=%s where name=%s", (highest, PREFIX))

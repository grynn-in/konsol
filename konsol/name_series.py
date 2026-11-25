"""Name series that never hand out a deleted name again (konsol#535, PR #603 F1).

Frappe's delete_doc reverts a `PREFIX-.#####` series when the deleted
document was the last one named from it (frappe/model/delete_doc.py
update_naming_series, after on_trash and before after_delete). For an upload
that would reuse the deleted name, and records that name the old upload by
string would then point at the new one. ``keep_series_above`` puts the
counter back; it only ever raises it.
"""
import re

import frappe


def keep_series_above(prefix, name):
    """Raise ``tabSeries[prefix]`` to the number in ``name``, never lower it."""
    m = re.fullmatch(re.escape(prefix) + r"(\d+)", name or "")
    if not m:
        return
    n = int(m.group(1))
    frappe.db.sql("update `tabSeries` set `current` = %s where name = %s and `current` < %s",
                  (n, prefix, n))

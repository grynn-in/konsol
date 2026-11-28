"""Every existing Cost Allocation Rule spreads its sender's pool
(konsol#269 phase 2b-2, Q5 = 5B: share of pool plus a declared rate, Deepak Pai
13 Nov 2026; Q4 = A, rates on the Rule Set by step, 27 Nov 2026).

Before 2b-2 a step could only split the pool, so the new required ``basis``
is stamped 'Share of Pool' on every row that has none. A stamped row is left
alone: a second run writes the same no-op.

patches.txt has no [post_model_sync] section, so the column may not exist yet
when this runs; the doctype is reloaded first in that case (the idiom of
set_cost_allocation_driver_source.py).
"""
import frappe

DOCTYPE = "Cost Allocation Rule"
SHARE_OF_POOL = "Share of Pool"


def execute():
    if not frappe.db.table_exists(DOCTYPE):
        return
    if not frappe.db.has_column(DOCTYPE, "basis"):
        frappe.reload_doc("epm", "doctype", "cost_allocation_rule")
    frappe.db.sql(
        f"update `tab{DOCTYPE}` set `basis` = %s where `basis` is null or `basis` = ''",
        (SHARE_OF_POOL,))

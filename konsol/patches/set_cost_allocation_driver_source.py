"""Every existing Cost Allocation Driver is an Uploaded Statistic driver
(konsol#269 phase 2b-1, Q3 = 3B: three driver sources, Deepak Pai 13 Nov 2026).

Before 2b-1 a driver's values could only come from a Cost Allocation
Statistic, so the new required ``source`` is stamped 'Uploaded Statistic' on
every row that has none. A stamped row is left alone: a second run writes the
same no-op.

patches.txt has no [post_model_sync] section, so the column may not exist yet
when this runs; the doctype is reloaded first in that case (the idiom of
stamp_rate_set_actual.py).
"""
import frappe

DOCTYPE = "Cost Allocation Driver"
UPLOADED = "Uploaded Statistic"


def execute():
    if not frappe.db.table_exists(DOCTYPE):
        return
    if not frappe.db.has_column(DOCTYPE, "source"):
        frappe.reload_doc("epm", "doctype", "cost_allocation_driver")
    frappe.db.sql(
        f"update `tab{DOCTYPE}` set `source` = %s where `source` is null or `source` = ''",
        (UPLOADED,))

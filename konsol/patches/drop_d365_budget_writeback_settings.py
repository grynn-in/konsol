"""Delete EPM Settings' retired D365 budget write-back values (konsol#348, Q9-a).

Deepak Pai decided on 14 Nov 2026 to retire the D365 budget write-back with
Budget Sheet (rejected: keep it). The fields left the doctype, but Frappe keeps
a Single's values in tabSingles, and a Password value in __Auth. The patch
deletes exactly those eight keys for EPM Settings, and the client secret's
__Auth row. A second run finds nothing and deletes nothing; no other key or
doctype is touched.
"""
import frappe

DOCTYPE = "EPM Settings"
KEYS = (
    "tab_d365_writeback",
    "d365_writeback_section",
    "enable_d365_budget_writeback",
    "d365_resource_url",
    "d365_tenant_id",
    "d365_client_id",
    "d365_client_secret",
    "d365_fiscal_year_start_month",
)


def execute():
    frappe.db.delete("Singles", {"doctype": DOCTYPE, "field": ("in", KEYS)})
    frappe.db.sql(
        "delete from `__Auth` where doctype = %s and name = %s and fieldname = %s",
        (DOCTYPE, DOCTYPE, "d365_client_secret"),
    )

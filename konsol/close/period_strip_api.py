"""Period strips endpoint: ``period_strip_info()`` (GET) returns the declared
fiscal years and the latest period with data (``period_strip_model.strip_info``)
so the Desk Year and Period strips can mark years and open on a period.

One warehouse read; if it fails (warehouse not built, any error) the latest
with data is None and the model falls back to the latest declared Regular
period. Never raises for a warehouse failure.
"""
import frappe

from konsol import fiscal_calendar
from konsol.close import ch_read, period_strip_model

_LATEST_SQL = (
    "SELECT fiscal_year, fiscal_period FROM epm_gold.gold_fully_consolidated_tb "
    "ORDER BY fiscal_year DESC, fiscal_period DESC LIMIT 1"
)


@frappe.whitelist(methods=["GET"])
def period_strip_info():
    frappe.only_for(("EPM Admin", "EPM Analyst", "EPM User", "System Manager"))
    period_rows = fiscal_calendar.fiscal_period_rows()
    latest = None
    try:
        data = ch_read.rows(_LATEST_SQL)
        if data:
            latest = (int(data[0]["fiscal_year"]), int(data[0]["fiscal_period"]))
    except Exception:  # noqa: BLE001 — "can't say" means no data period, never an error
        latest = None
    return period_strip_model.strip_info(period_rows, latest)

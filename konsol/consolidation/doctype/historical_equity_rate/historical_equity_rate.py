"""Historical Equity Rate — IAS 21 historical FX rates for equity accounts.

PRD-10: Equity accounts translated at the rate on the date the equity was
acquired/established, rather than closing rate.
"""
import frappe
from frappe.model.document import Document

from konsol.clickhouse import sync_doctype


class HistoricalEquityRate(Document):
    CH_TABLE = "epm_staging.historical_equity_rates"
    CH_FIELD_MAP = {
        "consolidation_group": "consolidation_group",
        "data_area_id": "data_area_id",
        "main_account": "main_account",
        "rate_date": "rate_date",
        "historical_rate": "historical_rate",
    }

    def on_submit(self):
        sync_doctype(self.doctype, self.CH_TABLE, self.CH_FIELD_MAP)

    def on_cancel(self):
        sync_doctype(self.doctype, self.CH_TABLE, self.CH_FIELD_MAP)

    def on_trash(self):
        sync_doctype(self.doctype, self.CH_TABLE, self.CH_FIELD_MAP)

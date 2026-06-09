"""IC Balance — intercompany sales and inventory balances between entity pairs.

PRD-15: Used by unrealized profit elimination (ending_inventory_from_ic × margin%).
"""
import frappe
from frappe.model.document import Document

from konsol.clickhouse import sync_doctype


class ICBalance(Document):
    CH_TABLE = "epm_staging.ic_balances"
    CH_FIELD_MAP = {
        "selling_entity": "selling_entity",
        "buying_entity": "buying_entity",
        "fiscal_year": "fiscal_year",
        "fiscal_period": "fiscal_period",
        "ic_sales_amount": "ic_sales_amount",
        "ending_inventory_from_ic": "ending_inventory_from_ic",
    }

    def on_submit(self):
        sync_doctype(self.doctype, self.CH_TABLE, self.CH_FIELD_MAP)

    def on_cancel(self):
        sync_doctype(self.doctype, self.CH_TABLE, self.CH_FIELD_MAP)

    def on_trash(self):
        sync_doctype(self.doctype, self.CH_TABLE, self.CH_FIELD_MAP)

"""Consolidation Adjustment — topside journals and reclassifications."""
import frappe
from frappe.model.document import Document

from konsol.clickhouse import sync_doctype


class ConsolidationAdjustment(Document):
    CH_TABLE = "gold.consolidation_adjustments"
    CH_FIELD_MAP = {
        "consolidation_group": "consolidation_group",
        "adjustment_type": "adjustment_type",
        "journal_id": "journal_id",
        "data_area_id": "data_area_id",
        "fiscal_year": "fiscal_year",
        "fiscal_period": "fiscal_period",
        "main_account": "main_account",
        "debit_amount": "debit_amount",
        "credit_amount": "credit_amount",
        "description": "description",
        "posted_by": "posted_by",
    }

    def on_update(self):
        sync_doctype(self.doctype, self.CH_TABLE, self.CH_FIELD_MAP)

    def on_trash(self):
        sync_doctype(self.doctype, self.CH_TABLE, self.CH_FIELD_MAP)

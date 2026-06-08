"""Spread Profile — allocation weights for budget top-down entry."""
import frappe
from frappe.model.document import Document

from konsol.clickhouse import sync_doctype


class SpreadProfile(Document):
    CH_TABLE = "gold.spread_profiles"
    CH_FIELD_MAP = {
        "profile_id": "profile_id",
        "profile_name": "profile_name",
        "fiscal_period": "fiscal_period",
        "weight": "weight",
    }

    def on_update(self):
        sync_doctype(self.doctype, self.CH_TABLE, self.CH_FIELD_MAP)

    def on_trash(self):
        sync_doctype(self.doctype, self.CH_TABLE, self.CH_FIELD_MAP)

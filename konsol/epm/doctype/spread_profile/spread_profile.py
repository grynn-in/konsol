"""Spread Profile — allocation weights for budget top-down entry."""
import frappe
from frappe.model.document import Document

from konsol.clickhouse import sync_doctype_after_commit


class SpreadProfile(Document):
    CH_TABLE = "epm_gold.spread_profiles"
    CH_FIELD_MAP = {
        "profile_id": "profile_id",
        "profile_name": "profile_name",
        "fiscal_period": "fiscal_period",
        "weight": "weight",
    }

    def on_update(self):
        sync_doctype_after_commit(self.doctype, self.CH_TABLE, self.CH_FIELD_MAP)

    def after_delete(self):
        """after_delete, not on_trash: on_trash runs before the row is gone, so
        the full-table re-send put it straight back (#120)."""
        sync_doctype_after_commit(self.doctype, self.CH_TABLE, self.CH_FIELD_MAP)

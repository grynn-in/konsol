"""Consolidation Group — entity groupings for financial consolidation."""
import frappe
from frappe.model.document import Document

from konsol.clickhouse import sync_doctype


class ConsolidationGroup(Document):
    CH_TABLE = "gold.consolidation_groups"
    CH_FIELD_MAP = {
        "consolidation_group": "consolidation_group",
        "data_area_id": "data_area_id",
        "entity_name": "entity_name",
        "ownership_pct": "ownership_pct",
        "reporting_currency": "reporting_currency",
        "consolidation_method": "consolidation_method",
    }

    def on_update(self):
        sync_doctype(self.doctype, self.CH_TABLE, self.CH_FIELD_MAP)

    def on_trash(self):
        sync_doctype(self.doctype, self.CH_TABLE, self.CH_FIELD_MAP)

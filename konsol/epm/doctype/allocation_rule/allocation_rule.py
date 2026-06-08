"""Allocation Rule — cost allocation rules synced to ClickHouse."""
import frappe
from frappe.model.document import Document

from konsol.clickhouse import sync_doctype


class AllocationRule(Document):
    CH_TABLE = "gold.allocation_rules"
    CH_FIELD_MAP = {
        "allocation_rule_id": "allocation_rule_id",
        "rule_name": "rule_name",
        "step_order": "step_order",
        "source_account": "source_account",
        "source_cost_center": "source_cost_center",
        "driver_type": "driver_type",
        "target_account": "target_account",
        "description": "description",
    }

    def on_update(self):
        sync_doctype(self.doctype, self.CH_TABLE, self.CH_FIELD_MAP)

    def on_trash(self):
        sync_doctype(self.doctype, self.CH_TABLE, self.CH_FIELD_MAP)

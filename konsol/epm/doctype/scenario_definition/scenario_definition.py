"""Scenario Definition — defines budget, forecast, and other scenarios."""
import frappe
from frappe.model.document import Document

from konsol.clickhouse import sync_doctype


class ScenarioDefinition(Document):
    CH_TABLE = "gold.scenario_definitions"
    CH_FIELD_MAP = {
        "scenario_id": "scenario_id",
        "scenario_name": "scenario_name",
        "scenario_type": "scenario_type",
        "is_active": "is_active",
    }

    def on_update(self):
        sync_doctype(self.doctype, self.CH_TABLE, self.CH_FIELD_MAP)

    def on_trash(self):
        sync_doctype(self.doctype, self.CH_TABLE, self.CH_FIELD_MAP)

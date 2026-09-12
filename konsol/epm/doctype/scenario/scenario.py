"""Scenario — defines budget, forecast, and other scenarios."""
import frappe
from frappe.model.document import Document

from konsol.clickhouse import sync_doctype_after_commit


class Scenario(Document):
    CH_TABLE = "epm_gold.scenario_definitions"
    CH_FIELD_MAP = {
        "scenario_id": "scenario_id",
        "scenario_name": "scenario_name",
        "scenario_type": "scenario_type",
        "is_active": "is_active",
    }

    def on_update(self):
        sync_doctype_after_commit(self.doctype, self.CH_TABLE, self.CH_FIELD_MAP)

    def after_delete(self):
        """after_delete, not on_trash: on_trash runs before the row is gone, so
        the full-table re-send put it straight back (#120)."""
        sync_doctype_after_commit(self.doctype, self.CH_TABLE, self.CH_FIELD_MAP)

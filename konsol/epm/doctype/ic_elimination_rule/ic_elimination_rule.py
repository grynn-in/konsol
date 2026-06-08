"""IC Elimination Rule — intercompany elimination rules for consolidation."""
import frappe
from frappe.model.document import Document

from konsol.clickhouse import sync_doctype


class ICEliminationRule(Document):
    CH_TABLE = "gold.ic_elimination_rules"
    CH_FIELD_MAP = {
        "rule_id": "rule_id",
        "rule_name": "rule_name",
        "debit_account": "debit_account",
        "credit_account": "credit_account",
        "debit_entity_pattern": "debit_entity_pattern",
        "credit_entity_pattern": "credit_entity_pattern",
        "description": "description",
    }

    def on_update(self):
        sync_doctype(self.doctype, self.CH_TABLE, self.CH_FIELD_MAP)

    def on_trash(self):
        sync_doctype(self.doctype, self.CH_TABLE, self.CH_FIELD_MAP)

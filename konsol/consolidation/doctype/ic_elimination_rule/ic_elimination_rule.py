"""IC Elimination Rule — intercompany elimination rules.

PRD-15: Extended with rule_type (balance/unrealized_profit), margin_pct, asset_account.
"""
import frappe
from frappe.model.document import Document

from konsol.clickhouse import sync_doctype


class ICEliminationRule(Document):
    # Legacy sync (seed replacement)
    CH_TABLE = "gold.ic_elimination_rules"
    CH_LEGACY_FIELD_MAP = {
        "rule_id": "rule_id",
        "rule_name": "rule_name",
        "debit_account": "debit_account",
        "credit_account": "credit_account",
        "debit_entity_pattern": "debit_entity_pattern",
        "credit_entity_pattern": "credit_entity_pattern",
        "description": "description",
    }

    # PRD-15: Staging sync with enhanced fields
    CH_STAGING_TABLE = "epm_staging.ic_elimination_rules"
    CH_STAGING_FIELD_MAP = {
        "rule_id": "rule_id",
        "rule_name": "rule_name",
        "debit_account": "debit_account",
        "credit_account": "credit_account",
        "debit_entity_pattern": "debit_entity_pattern",
        "credit_entity_pattern": "credit_entity_pattern",
        "description": "description",
        "rule_type": "rule_type",
        "margin_pct": "margin_pct",
        "asset_account": "asset_account",
    }

    def on_update(self):
        sync_doctype(self.doctype, self.CH_TABLE, self.CH_LEGACY_FIELD_MAP)
        sync_doctype(self.doctype, self.CH_STAGING_TABLE, self.CH_STAGING_FIELD_MAP)

    def on_trash(self):
        sync_doctype(self.doctype, self.CH_TABLE, self.CH_LEGACY_FIELD_MAP)
        sync_doctype(self.doctype, self.CH_STAGING_TABLE, self.CH_STAGING_FIELD_MAP)

"""Allocation Rule — cost allocation rules synced to ClickHouse.

PRD-17: Dynamic N-step engine (step_order driven)
PRD-18: allocation_method (step_down/reciprocal)
PRD-19: driver_formula for composite/conditional drivers
PRD-20: Tiered rules via child table
"""
import frappe
from frappe.model.document import Document

from konsol.clickhouse import sync_doctype, sync_table


class AllocationRule(Document):
    # Legacy sync (seed replacement)
    CH_TABLE = "epm_gold.allocation_rules"
    CH_LEGACY_FIELD_MAP = {
        "allocation_rule_id": "allocation_rule_id",
        "rule_name": "rule_name",
        "step_order": "step_order",
        "source_account": "source_account",
        "source_cost_center": "source_cost_center",
        "driver_type": "driver_type",
        "target_account": "target_account",
        "description": "description",
    }

    # PRD-17/18/19: Staging sync with method + formula fields
    CH_STAGING_TABLE = "epm_staging.allocation_rules"
    CH_STAGING_FIELD_MAP = {
        "allocation_rule_id": "allocation_rule_id",
        "rule_name": "rule_name",
        "step_order": "step_order",
        "source_account": "source_account",
        "source_cost_center": "source_cost_center",
        "driver_type": "driver_type",
        "target_account": "target_account",
        "description": "description",
        "allocation_method": "allocation_method",
        "driver_formula": "driver_formula",
    }

    def on_update(self):
        sync_doctype(self.doctype, self.CH_TABLE, self.CH_LEGACY_FIELD_MAP)
        sync_doctype(self.doctype, self.CH_STAGING_TABLE, self.CH_STAGING_FIELD_MAP)
        self._sync_tiers()

    def on_trash(self):
        sync_doctype(self.doctype, self.CH_TABLE, self.CH_LEGACY_FIELD_MAP)
        sync_doctype(self.doctype, self.CH_STAGING_TABLE, self.CH_STAGING_FIELD_MAP)
        self._sync_tiers()

    def _sync_tiers(self):
        """PRD-20: Sync all allocation tiers across all rules to epm_staging."""
        columns = [
            "allocation_rule_id", "tier_order", "lower_bound",
            "upper_bound", "rate", "cap", "floor",
        ]
        # Fetch all tier child rows across all Allocation Rule parents
        tiers = frappe.get_all(
            "Allocation Tier",
            fields=["parent as allocation_rule_id", "tier_order",
                     "lower_bound", "upper_bound", "rate", "cap", "floor_amount"],
            limit_page_length=0,
        )
        rows = [
            [t.allocation_rule_id, t.tier_order, t.lower_bound or 0,
             t.upper_bound or 999999999.99, t.rate or 1, t.cap or 999999999.99,
             t.floor_amount or 0]
            for t in tiers
        ]
        sync_table("epm_staging.allocation_tiers", columns, rows)

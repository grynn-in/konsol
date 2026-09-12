"""IC Elimination Rule — intercompany elimination rules.

PRD-15: Extended with rule_type (balance/unrealized_profit), margin_pct, asset_account.
"""
import frappe
from frappe.model.document import Document

from konsol.clickhouse import sync_doctype


class ICEliminationRule(Document):
    # konsolidat#146: the legacy epm_gold write-through is GONE. It existed to
    # replace a dbt seed — and seeds materialise into epm_gold, so the CSV and
    # this sync were the SAME ClickHouse relation, overwriting each other on
    # every `dbt seed` and every `bench migrate`. The seed is deleted and every
    # dbt reader moved to the staging table below, which is the richer one
    # anyway (the legacy map dropped the workflow/method columns entirely).

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
        sync_doctype(self.doctype, self.CH_STAGING_TABLE, self.CH_STAGING_FIELD_MAP)

    def on_trash(self):
        sync_doctype(self.doctype, self.CH_STAGING_TABLE, self.CH_STAGING_FIELD_MAP)

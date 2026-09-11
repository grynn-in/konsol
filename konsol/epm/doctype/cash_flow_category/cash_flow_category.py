"""Cash Flow Category — maps a balance-sheet GL account to a cash-flow line.

Mirrors Dimension Mapping: saves are pure metadata; Publish/Unpublish
re-syncs epm_staging.cash_flow_categories (consumed by gold_cash_flow_indirect
and gold_consolidated_cash_flow) and requests a governed rebuild. One Published
mapping per main_account. Spec: grynn-in/konsolidat#63.
"""
import frappe

from konsol.governed_reference import GovernedReferenceDocument


class CashFlowCategory(GovernedReferenceDocument):
    # F3: write-through replaces the CSV seed (see DimensionMapping). The
    # sync lives in the base class and reconcile_all honours the same
    # CH_SYNC_FILTERS, so a migrate can no longer re-fill this table with the
    # Draft and Inactive rows publish() deliberately withholds.
    CH_TABLE = "epm_staging.cash_flow_categories"
    CH_SYNC_FILTERS = {"status": "Published"}
    CH_FIELD_MAP = {
        "main_account": "main_account",
        "cf_category": "cf_category",
        "cf_line_item": "cf_line_item",
        "is_cash": "is_cash",
        "sign": "sign",
        "status": "status",
    }

    def validate(self):
        self._validate_unique_account()

    def _validate_unique_account(self):
        """One live cash-flow mapping per balance-sheet account.

        Enforced against other non-Inactive rows so an account never has two
        live cash-flow classifications (which would double-count it in the
        statement).
        """
        dupe = frappe.db.exists(
            "Cash Flow Category",
            {
                "main_account": self.main_account,
                "status": ["!=", "Inactive"],
                "name": ["!=", self.name],
            },
        )
        if dupe:
            frappe.throw(
                f"A cash-flow mapping for account '{self.main_account}' "
                f"already exists ({dupe})."
            )

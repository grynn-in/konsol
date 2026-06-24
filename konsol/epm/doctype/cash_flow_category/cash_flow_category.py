"""Cash Flow Category — maps a balance-sheet GL account to a cash-flow line.

Mirrors Dimension Mapping: saves are pure metadata; Publish/Unpublish
(re)generates seeds/cash_flow_categories.csv (consumed by gold_cash_flow_indirect
and gold_consolidated_cash_flow) and requests a governed rebuild. One Published
mapping per main_account. Spec: grynn-in/konsolidat#63.
"""
import frappe
from frappe.model.document import Document

from konsol.dbt_config import regenerate_cash_flow_categories_seed
from konsol.schema_lifecycle import check_epm_admin, request_governed_rebuild


class CashFlowCategory(Document):

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

    @frappe.whitelist()
    def publish(self):
        """Publish: regenerate the seed + request a governed rebuild."""
        check_epm_admin()
        self.status = "Published"
        self.save()
        regenerate_cash_flow_categories_seed()
        request_governed_rebuild(self, "Publish")

    @frappe.whitelist()
    def unpublish(self):
        """Unpublish (Inactive): regenerate seed + request a governed rebuild."""
        check_epm_admin()
        self.status = "Inactive"
        self.save()
        regenerate_cash_flow_categories_seed()
        request_governed_rebuild(self, "Unpublish")

    def after_delete(self):
        """Refresh the seed after a *Published* mapping is removed.

        Uses after_delete (not on_trash): on_trash runs before the row is gone,
        so regenerating there would still include the doc being deleted. Skipped
        during install/migrate/import — the seed is regenerated wholesale by
        after_migrate then, and no build should be enqueued mid-migrate.
        """
        if frappe.flags.in_install or frappe.flags.in_migrate or frappe.flags.in_patch:
            return
        if self.status == "Published":
            regenerate_cash_flow_categories_seed()
            request_governed_rebuild(self, "Delete")

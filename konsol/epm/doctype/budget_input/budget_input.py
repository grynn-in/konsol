"""Budget Input — parent doctype for monthly budget/forecast data.

Supports two input modes:
- Top-down: enter annual target + spread profile → "Spread" fills child rows
- Bottom-up: enter each month directly → annual_amount auto-computes

Budget layers (base/challenge/management/board) are additive — final budget
= sum of all layers for a given period. Each layer is role-controlled.
"""
import frappe
from frappe.model.document import Document

from konsol.clickhouse import sync_table

LAYER_ROLES = {
    "base": "Budget Submitter",
    "challenge": "Budget Controller",
    "management": "Budget Manager",
    "board": "Budget Approver",
}


class BudgetInput(Document):
    def validate(self):
        self._compute_annual_amount()
        self._validate_layer_permissions()

    def on_update(self):
        if self.workflow_state == "Approved":
            self._sync_to_clickhouse()

    def on_trash(self):
        self._sync_to_clickhouse()

    def _compute_annual_amount(self):
        """annual_amount = sum of all child period amounts."""
        self.annual_amount = sum(row.amount or 0 for row in self.periods)

    def _validate_layer_permissions(self):
        """Enforce layer-based edit permissions."""
        user_roles = frappe.get_roles()
        if "System Manager" in user_roles:
            return

        for row in self.periods:
            required_role = LAYER_ROLES.get(row.layer)
            if required_role and required_role not in user_roles:
                frappe.throw(
                    f"You need the '{required_role}' role to edit the "
                    f"'{row.layer}' budget layer.",
                    frappe.PermissionError,
                )

    def _sync_to_clickhouse(self):
        """Sync all approved Budget Input docs to ClickHouse."""
        columns = [
            "scenario_id", "data_area_id", "fiscal_year", "main_account",
            "dim_cost_center", "dim_department", "fiscal_period", "amount",
            "layer",
        ]
        docs = frappe.get_all(
            "Budget Input",
            filters={"workflow_state": "Approved"},
            fields=["name", "scenario_id", "data_area_id", "fiscal_year",
                     "main_account", "dim_cost_center", "dim_department"],
            limit_page_length=0,
        )
        rows = []
        for doc in docs:
            periods = frappe.get_all(
                "Budget Input Child",
                filters={"parent": doc.name},
                fields=["fiscal_period", "amount", "layer"],
                limit_page_length=0,
            )
            for p in periods:
                rows.append([
                    doc.scenario_id, doc.data_area_id, doc.fiscal_year,
                    doc.main_account, doc.dim_cost_center or "",
                    doc.dim_department or "", p.fiscal_period, p.amount,
                    p.layer,
                ])
        sync_table("gold.budget_monthly_input", columns, rows)

    @frappe.whitelist()
    def spread_annual(self):
        """Top-down entry: spread annual_amount across 12 periods using spread profile.

        Reads the selected spread_profile_id, fetches weights, and fills
        child rows proportionally. Existing child rows are replaced.
        """
        if not self.spread_profile_id:
            frappe.throw("Select a Spread Profile first.")

        profiles = frappe.get_all(
            "Spread Profile",
            filters={"profile_id": self.spread_profile_id},
            fields=["fiscal_period", "weight"],
            order_by="fiscal_period asc",
            limit_page_length=0,
        )
        if not profiles:
            frappe.throw(f"No weights found for profile '{self.spread_profile_id}'.")

        total_weight = sum(p.weight for p in profiles)
        if total_weight == 0:
            frappe.throw("Total weight is zero — cannot spread.")

        # Get current annual total from existing rows (or use manual input)
        annual = self.annual_amount or 0
        if annual == 0:
            frappe.throw("Enter period amounts first, or set amounts manually.")

        # Clear existing periods and replace with spread
        self.periods = []
        for p in profiles:
            self.append("periods", {
                "fiscal_period": p.fiscal_period,
                "amount": round(annual * p.weight / total_weight, 2),
                "layer": "base",
            })
        self._compute_annual_amount()

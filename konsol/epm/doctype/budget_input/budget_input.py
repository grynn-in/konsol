"""Budget Input — parent doctype for monthly budget/forecast data.

Supports two input modes:
- Top-down: enter annual target + spread profile → "Spread" fills child rows
- Bottom-up: enter each month directly → annual_amount auto-computes

Budget layers (base/challenge/management/board) are additive — final budget
= sum of all layers for a given period. Each layer is role-controlled.
"""
import frappe
from frappe.model.document import Document

from konsol.clickhouse import sync_rows

LAYER_ROLES = {
    "base": "Budget Submitter",
    "challenge": "Budget Controller",
    "management": "Budget Manager",
    "board": "Budget Approver",
}


class BudgetInput(Document):
    def autoname(self):
        """Name from the dimensional grain: fixed keys + in_budget dimensions.

        Spec #51 — the doc name *is* the unique key, so it must include the
        budget dimensions; otherwise two cost centers of the same account
        collide on one name. Takes precedence over any JSON ``autoname``
        (Frappe runs the controller method first; see model/naming.py).
        """
        from konsol.epm.budget_grain import budget_dimension_names, budget_name

        values = {
            "scenario_id": self.scenario_id,
            "data_area_id": self.data_area_id,
            "fiscal_year": self.fiscal_year,
            "main_account": self.main_account,
        }
        for dim in budget_dimension_names():
            values[dim] = self.get(dim)
        self.name = budget_name(values)

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
        """Incremental sync: only this doc's rows to ClickHouse.

        Deletes existing rows matching this doc's unique key, then inserts
        current period rows. Does NOT re-sync all approved docs.
        """
        budget_dims = frappe.get_all(
            "Dimension",
            filters={"in_budget": 1, "status": "Published"},
            fields=["dimension_name"],
            order_by="dimension_name asc",
            limit_page_length=0,
        )
        dim_names = [d.dimension_name for d in budget_dims]

        columns = [
            "scenario_id", "data_area_id", "fiscal_year", "main_account",
            *dim_names,
            "fiscal_period", "amount", "layer",
        ]

        # Unique key for this doc — must include the grain dimensions, else the
        # delete-by-key on incremental sync would wipe sibling rows that share
        # the account but differ by dimension (spec #51).
        key_columns = ["scenario_id", "data_area_id", "fiscal_year", "main_account", *dim_names]
        key_values = {
            "scenario_id": self.scenario_id,
            "data_area_id": self.data_area_id,
            "fiscal_year": int(self.fiscal_year),
            "main_account": self.main_account,
        }
        for dn in dim_names:
            key_values[dn] = self.get(dn) or ""

        # Build rows from this doc's periods only
        rows = []
        if self.workflow_state == "Approved":
            for p in self.periods:
                row = [
                    self.scenario_id, self.data_area_id, int(self.fiscal_year),
                    self.main_account,
                ]
                for dn in dim_names:
                    row.append(self.get(dn) or "")
                row.extend([p.fiscal_period, p.amount, p.layer])
                rows.append(row)

        # DELETE old rows for this key, INSERT new ones (empty rows on trash/unapprove)
        sync_rows("epm_gold.budget_monthly_input", columns, rows, key_columns, key_values)

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

"""Budget Sheet — one entity × layer worth of wide budget lines.

A sheet groups all of an entity's account/dimension `Budget Line` rows for one
budget layer under a parent `Budget Cycle`. It carries no workflow of its own —
editing is gated by the cycle lock (see `budget_cycle.BudgetCycle`). On lock the
cycle drives each sheet's `_sync_to_clickhouse` (wide→tall explode) and D365
write-back.
"""
import frappe
from frappe.model.document import Document

from konsol.clickhouse import sync_rows
from konsol.epm.budget_grain import (
    budget_dimension_names, digest_name, normalize_layer,
)
from konsol.epm.doctype.budget_line.budget_line import PERIOD_FIELDS

# Which role may own each budget layer. Editing a sheet of a given layer
# requires the matching role (ported from the old Budget Input layer model).
LAYER_ROLES = {
    "base": "Budget Submitter",
    "challenge": "Budget Controller",
    "management": "Budget Manager",
    "board": "Budget Approver",
}

CLICKHOUSE_TABLE = "epm_gold.budget_monthly_input"


class BudgetSheet(Document):
    def autoname(self):
        """Collision-safe name for the (cycle, entity, layer) grain.

        A raw ``format:`` autoname of long cycle/entity/layer codes can exceed
        Frappe's 140-char name column and silently truncate two distinct grains
        onto one name; ``digest_name`` appends a sha1 of the exact tuple so that
        can't happen (the get-or-create upsert relies on name uniqueness).
        """
        self.name = digest_name("BSHT", [self.cycle, self.data_area_id, self.layer])

    def validate(self):
        self._guard_cycle_locked()
        self._compute_totals()
        self._validate_layer_permission()

    def _guard_cycle_locked(self):
        """Reject edits once the parent cycle is locked.

        Belt-and-suspenders with the API write guard (`api.budget_cell_save`):
        a locked cycle means the budget is final. Cancel the cycle to amend.
        """
        if not self.cycle:
            return
        status = frappe.db.get_value("Budget Cycle", self.cycle, "status")
        if status == "Locked":
            frappe.throw(
                f"Budget Cycle '{self.cycle}' is locked; cancel it to amend.",
                frappe.ValidationError,
            )

    def _compute_totals(self):
        """Per-line annual = Σ period_01..12; sheet annual_total = Σ line annuals."""
        total = 0
        for line in self.lines:
            line.annual = sum(line.get(f) or 0 for f in PERIOD_FIELDS)
            total += line.annual
        self.annual_total = total

    def _validate_layer_permission(self):
        """Editing a sheet requires the role that owns its layer."""
        user_roles = frappe.get_roles()
        if "System Manager" in user_roles:
            return
        # normalize so a mis-cased layer ('Base') can't slip past the role gate.
        required_role = LAYER_ROLES.get(normalize_layer(self.layer))
        if required_role and required_role not in user_roles:
            frappe.throw(
                f"You need the '{required_role}' role to edit the "
                f"'{self.layer}' budget layer.",
                frappe.PermissionError,
            )

    # ------------------------------------------------------------------
    # ClickHouse sync — driven by the cycle on lock/unlock
    # ------------------------------------------------------------------

    def _sync_to_clickhouse(self, scenario_id, fiscal_year, active=True):
        """Wide→tall explode this sheet into ``epm_gold.budget_monthly_input``.

        Incremental key = (scenario, entity, fiscal_year, layer): one DELETE +
        INSERT replaces exactly this sheet's rows. ``active=False`` (cycle
        cancel) deletes with no re-insert — withdraws the sheet's budget.

        ``scenario_id`` / ``fiscal_year`` come from the parent Budget Cycle.
        """
        dim_names = budget_dimension_names()

        columns = [
            "scenario_id", "data_area_id", "fiscal_year", "main_account",
            *dim_names,
            "fiscal_period", "amount", "layer",
        ]
        key_columns = ["scenario_id", "data_area_id", "fiscal_year", "layer"]
        key_values = {
            "scenario_id": scenario_id,
            "data_area_id": self.data_area_id,
            "fiscal_year": int(fiscal_year),
            "layer": self.layer,
        }

        rows = []
        if active:
            for line in self.lines:
                for period, field in enumerate(PERIOD_FIELDS, start=1):
                    amount = line.get(field) or 0
                    # Skip zero months: the wide line always has 12 columns, but
                    # a 0 means "not budgeted" — emitting it would create phantom
                    # rows and diverge from D365 build_entries (which skips zeros).
                    if not amount:
                        continue
                    row = [
                        scenario_id, self.data_area_id, int(fiscal_year),
                        line.main_account,
                    ]
                    for dn in dim_names:
                        row.append(line.get(dn) or "")
                    row.extend([period, amount, self.layer])
                    rows.append(row)

        sync_rows(CLICKHOUSE_TABLE, columns, rows, key_columns, key_values)

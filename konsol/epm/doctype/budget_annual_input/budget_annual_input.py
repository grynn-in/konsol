"""Budget Annual Input — a top-down annual figure, spread into months by profile.

konsolidat#146: this was `seeds/budget_annual_input.csv`, the last seed the dbt
project owned. Like the ISO currency and fiscal-calendar lists it had no second
writer, but budget figures are not something a transform repo should hold.

konsol already owned the *bottom-up* path — Budget Cycle → Budget Sheet →
Budget Line, exploded into `epm_gold.budget_monthly_input` per period. This is
the other half: one annual number plus a Spread Profile, which
`gold_spread_budget` turns into twelve monthly rows. The two branches of that
model are now both konsol's.
"""
import frappe
from frappe.model.document import Document

from konsol.clickhouse import sync_doctype


class BudgetAnnualInput(Document):
    CH_TABLE = "epm_gold.budget_annual_input"
    CH_FIELD_MAP = {
        "scenario_id": "scenario_id",
        "data_area_id": "data_area_id",
        "fiscal_year": "fiscal_year",
        "main_account": "main_account",
        "dim_cost_center": "dim_cost_center",
        "dim_department": "dim_department",
        "annual_amount": "annual_amount",
        "spread_profile_id": "spread_profile_id",
        "submitted_by": "submitted_by",
    }

    def validate(self):
        self._stamp_submitter()
        self._guard_cycle_locked()

    def _stamp_submitter(self):
        """submitted_by is an audit field, not free text."""
        if not self.submitted_by:
            self.submitted_by = frappe.session.user

    def _guard_cycle_locked(self):
        """A locked Budget Cycle closes BOTH input paths, not just one.

        Budget Sheet — the bottom-up path — links a cycle, checks
        `_guard_cycle_locked`, and only reaches ClickHouse when the cycle locks.
        This is the top-down path into the SAME model (gold_spread_budget), so
        an ungated write here would be a hole straight through the budget lock:
        lock the cycle, then keep editing the annual figures.

        The cycle is derived rather than linked — Budget Cycle is keyed on
        (scenario_id, fiscal_year) and so is this — so no field is duplicated.
        Skipped during install/migrate/import: fixtures and patches load against
        whatever state the site is in.
        """
        if frappe.flags.in_install or frappe.flags.in_migrate or frappe.flags.in_import:
            return
        if not (self.scenario_id and self.fiscal_year):
            return
        locked = frappe.db.exists("Budget Cycle", {
            "scenario_id": self.scenario_id,
            "fiscal_year": self.fiscal_year,
            "status": "Locked",
        })
        if locked:
            frappe.throw(
                f"Budget Cycle {locked} is locked for {self.scenario_id} "
                f"{self.fiscal_year}. Reopen it before changing annual input."
            )

    def on_update(self):
        sync_doctype(self.doctype, self.CH_TABLE, self.CH_FIELD_MAP)

    def after_delete(self):
        """after_delete, NOT on_trash.

        ``sync_doctype`` re-sends the whole table from ``frappe.get_all``, and
        on_trash runs BEFORE the row is removed — so a delete would re-publish
        the row it just deleted and leave it live in the warehouse until
        something else resynced this doctype. Same reasoning as
        GovernedReferenceDocument.after_delete and the Connector registry
        (test_connector_registry, test_dimension_mapping).
        """
        sync_doctype(self.doctype, self.CH_TABLE, self.CH_FIELD_MAP)

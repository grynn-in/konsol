"""Budget Cycle — the single lock gate for a scenario × fiscal year budget.

The cycle is the *only* approval/lock construct: there is no per-line or
per-owner workflow. While the cycle is Open (docstatus 0) anyone with edit
rights writes budget cells via Excel; the application manager **submits** the
cycle (docstatus 1) at the deadline, which locks every sheet and fires the
ClickHouse sync + D365 write-back once per sheet. Cancel reopens for amendment.
"""
import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import now_datetime

from konsol.epm.budget_grain import digest_name


class BudgetCycle(Document):
    def validate(self):
        """Reject 'actual' scenarios.

        A cycle is the lock gate for *authored* plan data; actuals are
        GL-derived (gold_trial_balance, no scenario_id) and are never entered or
        locked here. Targeting an actual scenario would write budget cells into
        gold_spread_budget under scenario_id=ACTUAL, which nothing reads — a
        silent misconfiguration. The picker hides actual scenarios too
        (budget_cycle.js); this is the authoritative guard.
        """
        if not self.scenario_id:
            return  # mandatory check surfaces the empty value elsewhere
        scenario_type = frappe.db.get_value(
            "Scenario Definition", self.scenario_id, "scenario_type"
        )
        if scenario_type == "actual":
            frappe.throw(
                _(
                    "A Budget Cycle cannot target an 'actual' scenario — actuals "
                    "come from the GL, not from budget entry. Choose a budget or "
                    "forecast scenario."
                ),
                title=_("Invalid Scenario"),
            )

    def autoname(self):
        """Collision-safe name for the (scenario, fiscal_year) grain.

        Digest-suffixed so long scenario codes can't truncate two cycles onto
        one 140-char name (see budget_grain.digest_name)."""
        self.name = digest_name("BCYC", [self.scenario_id, self.fiscal_year])

    def on_submit(self):
        """Lock the cycle: freeze sheets, sync ClickHouse, push D365.

        Each sheet is isolated: one sheet's ClickHouse/enqueue failure is logged
        and skipped (its d365 status surfaces the gap) rather than aborting the
        whole lock and rolling back every other sheet's already-applied sync.
        """
        self.db_set("status", "Locked", update_modified=False)
        self.db_set("locked_by", frappe.session.user, update_modified=False)
        self.db_set("locked_on", now_datetime(), update_modified=False)

        for sheet in self._sheets():
            try:
                sheet._sync_to_clickhouse(self.scenario_id, self.fiscal_year, active=True)
                if self._d365_enabled_for(sheet.data_area_id):
                    from konsol.d365_writeback import enqueue_push_budget_sheet
                    enqueue_push_budget_sheet(sheet.name)
            except Exception:
                frappe.log_error(
                    title=f"Budget Cycle lock: sheet {sheet.name} sync/push failed",
                    message=frappe.get_traceback(),
                )

    def on_cancel(self):
        """Unlock the cycle: withdraw downstream and reopen sheets for editing."""
        self.db_set("status", "Open", update_modified=False)
        self.db_set("locked_by", None, update_modified=False)
        self.db_set("locked_on", None, update_modified=False)

        for sheet in self._sheets():
            try:
                sheet._sync_to_clickhouse(self.scenario_id, self.fiscal_year, active=False)
                if self._d365_enabled_for(sheet.data_area_id):
                    frappe.enqueue(
                        "konsol.d365_writeback.withdraw_budget_sheet",
                        queue="long",
                        name=sheet.name,
                    )
                elif sheet.meta.has_field("d365_writeback_status"):
                    # Write-back off: no withdraw job to clear status, so clear it
                    # here — else a stale 'Pushed' makes a later re-lock skip the push.
                    sheet.db_set("d365_writeback_status", "", update_modified=False)
            except Exception:
                frappe.log_error(
                    title=f"Budget Cycle unlock: sheet {sheet.name} withdraw failed",
                    message=frappe.get_traceback(),
                )

    # ------------------------------------------------------------------

    def _sheets(self):
        """Load this cycle's Budget Sheet docs."""
        names = frappe.get_all("Budget Sheet", filters={"cycle": self.name}, pluck="name")
        return [frappe.get_doc("Budget Sheet", n) for n in names]

    @staticmethod
    def _d365_enabled_for(entity_id):
        """Whether D365 budget write-back is enabled for an entity.

        Lazy import + defensive: returns False (not raise) when D365 config is
        absent, so locking a cycle never fails just because write-back is off.
        """
        try:
            from konsol.d365_writeback import get_config
            return bool(get_config(entity_id=entity_id).get("enabled"))
        except Exception:
            return False

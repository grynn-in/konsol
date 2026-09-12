"""Budget Cycle — the single lock gate for a scenario × fiscal year budget.

The cycle is the *only* approval/lock construct: there is no per-line or
per-owner workflow. While the cycle is Open (docstatus 0) anyone with edit
rights writes budget cells via Excel; the application manager **submits** the
cycle (docstatus 1) at the deadline, which locks every sheet and fires the
ClickHouse sync + D365 write-back once per sheet. Cancel reopens for amendment.
"""
import functools

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import now_datetime

from konsol.epm.budget_grain import digest_name
from konsol.clickhouse import after_commit_once


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
            "Scenario", self.scenario_id, "scenario_type"
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

    def before_submit(self):
        """Lock the cycle. Set here, before the submit is written, not with
        db_set in on_submit: nothing changes after submit (decided 12 Sep 2026;
        #136)."""
        self.status = "Locked"
        self.locked_by = frappe.session.user
        self.locked_on = now_datetime()

    def on_submit(self):
        """Push the locked sheets downstream, after the commit (#124/#136)."""
        after_commit_once(("budget_cycle", self.name, "lock"), functools.partial(self._push_sheets, True))

    def before_cancel(self):
        """Unlock the cycle: reopen its sheets for editing."""
        self.status = "Open"
        self.locked_by = None
        self.locked_on = None

    def on_cancel(self):
        """Withdraw the sheets downstream, after the commit (#124/#136)."""
        for sheet in self._sheets():
            if not self._d365_enabled_for(sheet.data_area_id) and sheet.meta.has_field("d365_writeback_status"):
                # Write-back off: no withdraw job will clear the status, so clear
                # it here, inside the transaction; else a stale 'Pushed' makes a
                # later re-lock skip the push. (A write after the commit would
                # never be committed.)
                sheet.db_set("d365_writeback_status", "", update_modified=False)
        after_commit_once(("budget_cycle", self.name, "unlock"), functools.partial(self._push_sheets, False))

    def _push_sheets(self, active):
        """Sync every sheet to ClickHouse and queue its D365 push (lock) or
        withdraw (unlock). Runs after the commit, so a sheet synced here can't
        outlive a lock that rolled back. Each sheet is isolated: one sheet's
        failure is logged and the rest proceed. The log is deferred, because
        this runs after the last commit."""
        for sheet in self._sheets():
            try:
                sheet._sync_to_clickhouse(self.scenario_id, self.fiscal_year, active=active)
                if self._d365_enabled_for(sheet.data_area_id):
                    if active:
                        from konsol.d365_writeback import enqueue_push_budget_sheet

                        enqueue_push_budget_sheet(sheet.name)
                    else:
                        frappe.enqueue("konsol.d365_writeback.withdraw_budget_sheet", queue="long", name=sheet.name)
            except Exception:
                frappe.log_error(
                    title=f"Budget Cycle {'lock' if active else 'unlock'}: sheet {sheet.name} sync/push failed",
                    message=frappe.get_traceback(),
                    defer_insert=True,
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

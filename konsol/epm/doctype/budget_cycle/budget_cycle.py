"""Budget Cycle: the approval of a scenario x fiscal year budget (BUD-9-3).

Submit is the approval (decided 12 Sep 2026). The installed workflow
(budget_cycle_workflow.json) takes a Cycle from Draft to Pending Approval
(EPM Analyst sends it) and Approve submits it (EPM Admin); Reject returns it
to Draft. Budget Entries and Annual Inputs are written while the Cycle is a
draft, and reach the warehouse while it is approved (docstatus 1): the
submit and the cancel queue their syncs, after the commit.

The deadline is a target date; nothing locks at it. A cancel is refused once
every period of the year is closed (period_status.assert_year_not_closed); a
cancelled Cycle leaves the warehouse and stays as the audit trail, and its
amendment carries the pair on.
"""
import frappe
from frappe import _
from frappe.model.document import Document

from konsol.epm.budget_grain import digest_name


class BudgetCycle(Document):
    def validate(self):
        """Reject 'actual' scenarios.

        A cycle approves *authored* plan data; actuals are
        GL-derived (gold_trial_balance, no scenario_id) and are never entered or
        locked here. Targeting an actual scenario would write budget cells into
        gold_spread_budget under scenario_id=ACTUAL, which nothing reads — a
        silent misconfiguration. The picker hides actual scenarios too
        (budget_cycle.js); this is the authoritative guard.
        """
        self.entry_count = self._count_entries()
        if not self.scenario_id:
            return  # mandatory check surfaces the empty value elsewhere
        # The copy the Planning sidebar filters on, taken under a share lock
        # so it cannot race a type change (review of #460).
        from konsol.epm.doctype.scenario.scenario import locked_scenario_type

        self.scenario_type = locked_scenario_type(self.scenario_id)
        scenario_type = self.scenario_type
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

    def onload(self):
        """Entries change without a Cycle save, so the form counts them when
        it opens."""
        self.entry_count = self._count_entries()

    def on_submit(self):
        """Approved: the Cycle's rows enter the warehouse, after the commit."""
        self._resync_annual_input()
        self._resync_entries()

    def before_cancel(self):
        """BUD-9-3: cancel only while the year has an open period. Nothing
        changes before the gate."""
        from konsol.period_status import assert_year_not_closed

        assert_year_not_closed(self.fiscal_year, _("cancel Budget Cycle {0}").format(self.name))

    def on_cancel(self):
        """Cancelled: the Cycle's rows leave the warehouse, after the commit."""
        self._resync_annual_input()
        self._resync_entries()

    @staticmethod
    def _resync_annual_input():
        """Queue the Budget Annual Input sync, once per transaction, after the
        commit (BUD-4). Its rows reach ClickHouse only while their cycle has
        docstatus 1 (CH_SYNC_FILTERS), so they change membership when a cycle
        is submitted or cancelled, not when they are saved. The sync replaces
        the whole table, so one sync covers every cycle in the transaction:
        the key is the doctype's own, shared with Annual Input saves."""
        from konsol.epm.doctype.budget_annual_input.budget_annual_input import queue_sync

        queue_sync()

    @staticmethod
    def _resync_entries():
        """Queue the Budget Entry Line sync, once per transaction, after the
        commit (S2-T2). Lines reach ClickHouse only while their cycle has
        docstatus 1 (CH_SYNC_FILTERS), so, like Annual Input, they change
        membership on a cycle submit or cancel, never on an entry save. The
        sync replaces the whole table, so one covers every cycle in the
        transaction."""
        from konsol.epm.doctype.budget_entry_line.budget_entry_line import queue_sync

        queue_sync()

    def _count_entries(self):
        """The Budget Entries under this Cycle: counted, never typed."""
        if not self.name:
            return 0
        return frappe.db.count("Budget Entry", {"cycle": self.name})


def on_doctype_update():
    """The index this doctype's budget locking read runs on (PR #522 review;
    konsol.budget_intake.LOCK_INDEXES)."""
    from konsol.budget_intake import add_lock_index

    add_lock_index("Budget Cycle")

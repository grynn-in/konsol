"""Budget Cycle: the approval of a scenario x fiscal year budget (BUD-9-3).

Submit is the approval (decided 12 Sep 2026). The installed workflow
(budget_cycle_workflow.json) takes a Cycle from Draft to Pending Approval
(EPM Analyst sends it) and Approve submits it (EPM Admin); Reject returns it
to Draft. Budget Entries and Annual Inputs are written while the Cycle is a
draft, and reach the warehouse while it is approved (docstatus 1): the
submit and the cancel queue their syncs, after the commit.

The deadline is a target date; nothing locks at it. A cancel is refused once
every period of the year is closed (period_status.assert_year_not_closed),
while a submitted Approved Budget names the Cycle (konsol#348 S5 K16, S5-Q5 a:
cancel the declaration first), and while a submitted Current Forecast names
it (S6 KA3, S6-Q1 b: the same); a cancelled Cycle leaves the warehouse and
stays as the audit trail, and its amendment carries the pair on.

A Cycle of a forecast scenario is one ROUND, keyed by (scenario, fiscal year,
as-of date) (#348 S3, Q23 a): it needs ``as_of_date`` and ``horizon_periods``,
which a Cycle of any other type refuses (QB8 a: only forecasts have rounds;
the rule is budget_bulk_model.cycle_round_problems). The one-live-Cycle rule
holds per round, so the rounds of one pair live side by side, and its name
carries the as-of date. A rolling round that crosses the year end is one
Cycle per fiscal year, sharing the as-of date (Q24 a).

Every Cycle names the Rate Set its budget is translated at (S4 K5, BUD-3:
declared per Cycle, no default; the rule is budget_bulk_model
.cycle_rate_set_problems). It is editable while the Cycle is a draft and
frozen by the submit: not set-once, because unlike the scenario, the year and
the as-of date it is not part of the Cycle's identity or name, and a draft's
set may be corrected before anyone approves it; never allow_on_submit, because
the Input Fingerprint stamped at Approve covers the set and its rates.

An approved Cycle is one row of ``epm_staging.budget_cycles`` (S4 K6, S4-Q2 a):
it enters on submit and leaves on cancel, after the commit, so the warehouse
reads the Rate Set, the scenario and the round of the ``cycle`` its fact rows
name; reconcile_all repairs the table like every other.

Approve stamps ``input_fingerprint`` (S4 K8, S4-Q5 a; BUD-11-3): the hash of
the inputs approved (konsol.epm.budget_fingerprint), read-only and frozen by
the submit. ``check_input_fingerprint`` recomputes it on request and reports a
mismatch; it decides nothing.

A forecast pair holds its approved rounds side by side (S6, FC-4), and no
reader sums them: the round a (scenario, year) reports is DECLARED in a
Current Forecast (S6-Q1 b, Deepak Pai, 17 Nov 2026), and the warehouse's
gold_forecast_current_round joins that declaration into every forecast
reader. The one-approved-round refusal that held the readers safe between
S3 and S6 (15 Nov 2026) is gone with konsolidat's backstop test; the two
halves of S6 pair A merged together so no gap summed two rounds.
"""
import frappe
from frappe import _
from frappe.model.document import Document

from konsol import budget_bulk_model as M
from konsol.epm.budget_grain import digest_name
from konsol.workflows import set_cancelled_state


class BudgetCycle(Document):
    #: S4 K6 (S4-Q2 a): the approved Cycles, one row each, so the warehouse
    #: reads a Cycle's Rate Set, scenario and round by the `cycle` name on its
    #: fact rows. Submitted only, by the doctype's is_submittable
    #: (resolve_sync_filters). Keys in the DDL's column order
    #: (clickhouse._REFERENCE_TABLE_DDL, identical to konsolidat's init-db.sql).
    #: Not scenario_type: the warehouse reads it from scenario_definitions by
    #: scenario_id; a copy here would be a second source of truth.
    CH_TABLE = "epm_staging.budget_cycles"
    CH_FIELD_MAP = {
        "name": "name",
        "scenario_id": "scenario_id",
        "fiscal_year": "fiscal_year",
        "rate_set": "rate_set",
        "as_of_date": "as_of_date",
        "horizon_periods": "horizon_periods",
    }

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
        self._refuse_bad_round()
        self._refuse_second_live_cycle()

    def _refuse_bad_round(self):
        """S3 K3: a forecast Cycle needs its round (as-of date, horizon of 1 or
        more); any other type refuses the round fields (Q23 a, QB8 a). S4 K5
        (BUD-3): every Cycle names the Rate Set it is translated at; nothing
        defaults it (budget_bulk_model.cycle_rate_set_problems). Every problem
        is told together, before the pair is locked: a Cycle whose round is
        not stated cannot be judged against the pair's rounds."""
        problems = M.cycle_round_problems(self.scenario_type, self.get("as_of_date"),
                                          self.get("horizon_periods"))
        problems += M.cycle_rate_set_problems(self.scenario_type, self.get("rate_set"))
        if problems:
            frappe.throw("\n".join(problems), frappe.ValidationError)

    def autoname(self):
        """Collision-safe name for the round: (scenario, fiscal_year), plus the
        as-of date for a forecast (S3 K4), so two rounds of one pair get
        different base names and neither reads as the other's amendment.

        Digest-suffixed so long scenario codes can't truncate two cycles onto
        one 140-char name (see budget_grain.digest_name). The digest when it
        is free, else the first free ``<digest>-N`` (#524): a cancelled Cycle
        keeps its name as the audit trail, and the next Cycle of its round
        must not collide with it. Every Cycle is named here, an amendment
        too: set_new_name below makes sure of it. Nothing finds a Cycle by
        this name: every writer finds the round's live Cycle by docstatus."""
        base = digest_name("BCYC", self._name_parts())
        name, n = base, 0
        while frappe.db.exists("Budget Cycle", name):
            n += 1
            name = f"{base}-{n}"
        self.name = name

    def _name_parts(self):
        """The key the name digests: (scenario, year) for every type, and the
        as-of date too for a forecast (its round). The type is the Scenario's,
        read when the fetched copy is not on the document yet (an API insert
        reaches autoname before validate copies it); a date on a non-forecast
        is left out, because validate refuses it anyway."""
        kind = self.get("scenario_type")
        if not kind:
            from konsol.epm.doctype.scenario.scenario import locked_scenario_type

            kind = locked_scenario_type(self.scenario_id)
        parts = [self.scenario_id, self.fiscal_year]
        if kind == M.FORECAST:
            parts.append(M.as_of_key(self.get("as_of_date")))
        return parts

    def set_new_name(self, *args, **kwargs):
        """The Cycle owns its name, under any site naming setting.

        Frappe v16 names an amendment by its amend counter before it reaches
        the controller (frappe/model/naming.py:177-179, _set_amended_name at
        :549): ``<base of amended_from>-<n+1>``, without checking that the
        name is free. A fresh Cycle of the pair may already hold it through
        autoname, so the insert would fail on a duplicate name: amend D after
        a fresh Cycle took D-1, or amend D-1 after one took D-2. A Document
        Naming Rule would reach the name before autoname too (naming.py:186).
        So, once Frappe has named the Cycle (Document.set_new_name,
        model/document.py:969), autoname names it again: the first free
        ``<digest>-N``. A name the caller passes (``set_name``), or a Cycle
        already named, is left alone."""
        force = kwargs.get("force", args[0] if args else False)
        set_name = kwargs.get("set_name", args[1] if len(args) > 1 else None)
        named = self.flags.name_set and not force
        super().set_new_name(*args, **kwargs)
        if not named and not set_name:
            self.autoname()

    def _refuse_second_live_cycle(self):
        """A round holds one live Cycle (docstatus 0 or 1): every budget
        writer takes the round's draft, and the warehouse would add two
        approved Cycles of one round together. The round is the (scenario,
        year) pair for a budget, and (scenario, year, as-of date) for a
        forecast (S3 K3, Q23 a: budget_bulk_model.in_round), so the rounds of
        one forecast pair live side by side. A cancelled Cycle does not count
        (19-b), so a cancelled round can be amended (#524). Read under the
        lock budget_intake.lock_cycles takes on the whole pair (QB9 a), the
        Cycle-first order every budget writer keeps, so two inserts for one
        round cannot both pass. Install and migrate skip it, as lock_cycles
        does."""
        from konsol import budget_intake

        states = {0: "draft", 1: "approved"}
        as_of = self.get("as_of_date")
        for c in budget_intake.lock_cycles(self.scenario_id, self.fiscal_year):
            if c["name"] == self.name or c["docstatus"] not in states:
                continue
            if not M.in_round(self.scenario_type, c, as_of):
                continue
            if self.scenario_type == M.FORECAST:
                message = _("Budget Cycle {0} is already the {1} Cycle of {2} {3} as of {4}. A forecast "
                            "scenario, year and as-of date hold one live Cycle: work in it, or cancel it "
                            "and amend it.").format(c["name"], states[c["docstatus"]], self.scenario_id,
                                                    self.fiscal_year, M.as_of_key(as_of))
            else:
                message = _("Budget Cycle {0} is already the {1} Cycle of {2} {3}. A scenario and year hold "
                            "one live Cycle: work in it, or cancel it and amend it.").format(
                    c["name"], states[c["docstatus"]], self.scenario_id, self.fiscal_year)
            frappe.throw(message, frappe.ValidationError)

    def onload(self):
        """Entries change without a Cycle save, so the form counts them when
        it opens."""
        self.entry_count = self._count_entries()

    def before_submit(self):
        """Approve is the submit (the workflow's Approve calls doc.submit(),
        frappe/model/workflow.py): the input fingerprint is stamped here
        (S4 K8, S4-Q5 a), the hash of the inputs being approved, set after
        validate and before anything is written so it lands with the approval
        and nothing saves in on_submit. A forecast pair's second approved
        round is accepted (S6 KA3): the readers take the declared current
        round (Current Forecast) and never sum rounds. Imported here: the
        controller is loaded under test without it."""
        from konsol.epm.budget_fingerprint import compute

        self.input_fingerprint = compute(self)

    def on_submit(self):
        """Approved: the Cycle and its rows enter the warehouse, after the commit."""
        self._resync_cycle()
        self._resync_annual_input()
        self._resync_entries()

    def before_cancel(self):
        """BUD-9-3: cancel only while the year has an open period, and never
        while a submitted Approved Budget (S5 K16, S5-Q5 a) or a submitted
        Current Forecast (S6 KA3, S6-Q1 b) names this Cycle. Nothing changes
        before the gates."""
        from konsol.period_status import assert_year_not_closed

        assert_year_not_closed(self.fiscal_year, _("cancel Budget Cycle {0}").format(self.name))
        self._refuse_cancel_under_an_approved_budget()
        self._refuse_cancel_under_a_current_forecast()
        # After the gates: the installed workflow's docstatus-2 state (#529).
        set_cancelled_state(self)

    def _refuse_cancel_under_an_approved_budget(self):
        """konsol#348 S5 K16 (S5-Q5 a): a submitted Approved Budget names the
        Cycle whose rows the Consolidated Statement's Budget columns read.
        Cancelling the Cycle under it would turn that budget into a
        read-time state ("the approved Cycle is cancelled") with nothing
        refused, so the cancel is refused here, naming the declaration to
        cancel first. A draft declaration declares nothing yet and a
        cancelled one is history: neither counts. Submitted rows only
        (docstatus 1), in name order.

        A LOCKING read (PR #554 re-review): a declaration's submit locks this
        Cycle's row (ApprovedBudget._cycle_row), so a cancel that waited on
        that lock reads here after the submit committed; a plain read would
        use this request's REPEATABLE READ snapshot, miss the new declaration
        and let both commit. FOR UPDATE reads the latest committed rows.
        frappe.get_all takes no for_update on v16, hence db.get_values."""
        live = frappe.db.get_values(
            "Approved Budget", {"budget_cycle": self.name, "docstatus": 1},
            ["name", "consolidation_group", "fiscal_year"], as_dict=True,
            order_by="name asc", for_update=True)
        if not live:
            return
        named = ", ".join(
            _("Approved Budget {0} ({1} FY{2})").format(r.name, r.consolidation_group, r.fiscal_year)
            for r in live)
        frappe.throw(
            _("Cannot cancel Budget Cycle {0}: {1} names it as the group's approved budget, and the "
              "Consolidated Statement's Budget columns read it. Cancel that Approved Budget first "
              "(a period of its year must be open), then the Cycle.").format(self.name, named),
            frappe.ValidationError)

    def _refuse_cancel_under_a_current_forecast(self):
        """konsol#348 S6 KA3 (S6-Q1 b, Deepak Pai, 17 Nov 2026): a submitted
        Current Forecast names this Cycle as the round its (scenario, year)
        reports; every forecast reader joins that declaration. Cancelling the
        Cycle under it would leave the declaration naming a cancelled round:
        gold_forecast_current_round drops the pair and the year reads empty
        with only a WARN to say why. So the cancel is refused here, naming
        the declaration to cancel first. A draft declaration declares nothing
        yet and a cancelled one is history: neither counts. Submitted rows
        only (docstatus 1), in name order.

        A LOCKING read, as the Approved Budget gate above: the declaration's
        submit locks this Cycle's row (CurrentForecast._cycle_row
        for_update), so a cancel that waited on that lock reads here after
        the submit committed; a plain read would use this request's
        REPEATABLE READ snapshot and miss it. frappe.get_all takes no
        for_update on v16, hence db.get_values."""
        live = frappe.db.get_values(
            "Current Forecast", {"budget_cycle": self.name, "docstatus": 1},
            ["name", "scenario_id", "fiscal_year"], as_dict=True,
            order_by="name asc", for_update=True)
        if not live:
            return
        named = ", ".join(
            _("Current Forecast {0} ({1} FY{2})").format(r.name, r.scenario_id, r.fiscal_year)
            for r in live)
        frappe.throw(
            _("Cannot cancel Budget Cycle {0}: {1} names it as the current forecast round, and every "
              "forecast read of that scenario and year takes it. Cancel that Current Forecast first "
              "(a period of its year must be open), then the Cycle.").format(self.name, named),
            frappe.ValidationError)

    def on_cancel(self):
        """Cancelled: the Cycle and its rows leave the warehouse, after the commit."""
        self._resync_cycle()
        self._resync_annual_input()
        self._resync_entries()

    def _resync_cycle(self):
        """Queue the Budget Cycle sync, once per transaction, after the commit
        (S4 K6). The sync replaces the whole table with the submitted Cycles,
        so one covers every Cycle in the transaction; a draft save queues
        nothing, because a draft is not in the table either way. Imported
        here, as the two syncs below import theirs: the controller is loaded
        under test without clickhouse.py."""
        from konsol.clickhouse import sync_doctype_after_commit

        sync_doctype_after_commit(self.doctype, self.CH_TABLE, self.CH_FIELD_MAP)

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

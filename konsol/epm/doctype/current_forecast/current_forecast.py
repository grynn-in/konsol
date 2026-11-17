"""Current Forecast: the declaration that one approved forecast round is THE
round a forecast scenario reports for a fiscal year (konsol#348 S6 KA1;
S6-Q1 (b), Deepak Pai, 17 Nov 2026, #348 comment 6953756459).

A forecast (scenario, fiscal year) holds several approved rounds side by side
(Q23 a, FC-4), and rounds never sum. Every entity-grain reader needs one
round: the scenario trial balance (gold_scenario_trial_balance), the Excel
flat and hierarchy forecast reads, the cost-allocation pool. FC-3-1 (decided
13 Nov 2026) rejected "latest round wins", so the round is declared here and
written through to ``epm_staging.current_forecasts``; the warehouse's
gold_forecast_current_round reads the declaration and every forecast reader
joins it. Undeclared, a year has no forecast rows anywhere and
assert_forecast_year_has_current_round WARNs naming it: nothing is summed and
nothing is guessed. The declaration adds no figures.

Submittable, and submit is the approval (the Approved Budget precedent, #348
S5-Q1 a): review happens on the draft, EPM Admin submits, there is no
installed workflow and no state field. The approver and the time are stamped
at submit (``approved_by``, ``approved_on``), with a copy of the round's
``input_fingerprint`` (BUD-11-3). Cancel is gated on the year (BUD-9-3, the
Budget Cycle rule); a cancelled declaration stays as the audit trail and may
be amended.

Write-through (S6 KA2): a submitted declaration is one row of
``epm_staging.current_forecasts`` (scenario_id, fiscal_year, cycle; the
warehouse reads the round's as-of from budget_cycles), entering on submit and
leaving on cancel, after the commit, once per transaction (clickhouse.sync_doctype_after_commit); submitted rows only,
by the doctype's own is_submittable (resolve_sync_filters). The DDL is
clickhouse._REFERENCE_TABLE_DDL, identical to konsolidat's init-db.sql.

validate refuses, by name, what the readers could not read correctly:
- a scenario that is not of type ``forecast`` (read under the Scenario share
  lock, review of #460): a budget scenario has one Cycle per year by rule,
  and Approved Budget is its declaration;
- a Cycle that is not approved, of another scenario, or of another year
  (Q24 a: a round crossing the year end is one Cycle per fiscal year, and
  each year declares its own);
- a second live declaration (docstatus 0 or 1) for the (scenario, year), read
  FOR UPDATE: under REPEATABLE READ a plain read would miss one committed
  after this transaction's first read (Approved Budget's
  _refuse_second_live_declaration). A cancelled declaration does not count,
  so a cancelled one can be amended.
"""
import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import now_datetime

from konsol.epm.budget_grain import digest_name
from konsol.workflows import set_cancelled_state

FORECAST = "forecast"

#: The index validate's FOR UPDATE read runs on (PR #522 review: an unindexed
#: locking read scans the table and next-key-locks every row).
LIVE_INDEX = ("scenario_id", "fiscal_year", "docstatus")
LIVE_INDEX_NAME = "current_forecast_live"

#: The Cycle columns the declaration reads: its state, its identity and the
#: read-only copies the form shows.
_CYCLE_FIELDS = ["docstatus", "scenario_id", "scenario_type", "fiscal_year", "as_of_date",
                 "horizon_periods", "rate_set", "input_fingerprint"]


class CurrentForecast(Document):
    #: S6 KA2: the declared current round, one row per submitted declaration,
    #: so gold_forecast_current_round picks the round every forecast reader
    #: joins. Keys are ClickHouse columns in the DDL's order
    #: (clickhouse._REFERENCE_TABLE_DDL, identical to konsolidat's
    #: init-db.sql); values are this doctype's fields. as_of_date,
    #: horizon_periods and rate_set are not here: the warehouse reads them
    #: from budget_cycles by `cycle`, and a copy would be a second source of
    #: truth (S6 pair A review m4). The form keeps them, read-only.
    CH_TABLE = "epm_staging.current_forecasts"
    CH_FIELD_MAP = {
        "scenario_id": "scenario_id",
        "fiscal_year": "fiscal_year",
        "cycle": "budget_cycle",
    }

    def validate(self):
        self._refuse_non_forecast_scenario()
        cycle = self._cycle_row()
        self._refuse_bad_cycle(cycle)
        # Read-only copies of the round, set here so an API insert that skips
        # fetch_from carries them too; the Cycle's own are the truth.
        self.as_of_date = cycle.as_of_date
        self.horizon_periods = cycle.horizon_periods
        self.rate_set = cycle.rate_set
        self.title = f"{self.scenario_id} FY{self.fiscal_year}"
        self._refuse_second_live_declaration()

    def _refuse_non_forecast_scenario(self):
        """S6-Q1 (b): only a forecast has rounds to choose between (QB8 a).
        The type is read under the Scenario's share lock (review of #460), so
        a declaration cannot race a type change. Imported here: the
        controller is loaded under test with the module stubbed."""
        from konsol.epm.doctype.scenario.scenario import locked_scenario_type

        kind = locked_scenario_type(self.scenario_id)
        if kind is None:
            frappe.throw(_("Scenario {0} does not exist.").format(self.scenario_id), frappe.ValidationError)
        if kind != FORECAST:
            frappe.throw(
                _("Scenario {0} is a {1} scenario. A Current Forecast declares the round of a forecast "
                  "scenario; a budget scenario has one Cycle per year, and Approved Budget is its "
                  "declaration.").format(self.scenario_id, kind),
                frappe.ValidationError)

    def _cycle_row(self, for_update=False):
        """The named Cycle's row. ``for_update`` (before_submit, S6 KA2) locks
        it, so this submit and Budget Cycle's cancel gate (which reads the
        submitted declarations naming the Cycle FOR UPDATE) serialise instead
        of both passing under REPEATABLE READ (Approved Budget._cycle_row has
        the trace). validate's read stays plain."""
        row = frappe.db.get_value("Budget Cycle", self.budget_cycle, _CYCLE_FIELDS,
                                  as_dict=True, for_update=for_update)
        if row is None:
            frappe.throw(_("Budget Cycle {0} does not exist.").format(self.budget_cycle), frappe.ValidationError)
        return row

    def _refuse_bad_cycle(self, cycle):
        """The declaration names an approved Cycle of its own scenario and
        year. A draft or cancelled one has nothing approved to declare; a
        Cycle of another scenario is another forecast's round; a Cycle of
        another year is the round's sibling for that year (Q24 a), which that
        year declares for itself."""
        states = {0: _("a draft"), 2: _("cancelled")}
        if int(cycle.docstatus or 0) != 1:
            frappe.throw(
                _("Budget Cycle {0} is {1}, not approved. A Current Forecast names an approved round: approve "
                  "it first, or choose another.").format(self.budget_cycle, states.get(int(cycle.docstatus or 0),
                                                                                         _("not approved"))),
                frappe.ValidationError)
        if cycle.scenario_id != self.scenario_id:
            frappe.throw(
                _("Budget Cycle {0} is a round of scenario {1}; this declaration is for {2}. Choose a Cycle of "
                  "the same scenario.").format(self.budget_cycle, cycle.scenario_id, self.scenario_id),
                frappe.ValidationError)
        if int(cycle.fiscal_year or 0) != int(self.fiscal_year or 0):
            frappe.throw(
                _("Budget Cycle {0} is for FY{1}; this declaration is for FY{2}. Choose the round's Cycle of the "
                  "same year.").format(self.budget_cycle, cycle.fiscal_year, self.fiscal_year),
                frappe.ValidationError)

    def _refuse_second_live_declaration(self):
        """One live declaration (docstatus 0 or 1) per (scenario, year): the
        readers read THE current round, and two would make the choice for
        them. Read FOR UPDATE on (scenario_id, fiscal_year, docstatus), the
        index on_doctype_update adds, so two inserts for one pair serialise
        and the second sees the first (the REPEATABLE READ trap: a plain read
        serves the transaction's snapshot). The document's own row does not
        count on a re-save; a cancelled one never does, so a cancelled
        declaration can be amended. Skipped in install and migrate, as
        Approved Budget's is."""
        if frappe.flags.in_install or frappe.flags.in_migrate:
            return
        states = {0: _("draft"), 1: _("approved")}
        rows = frappe.db.get_values(
            "Current Forecast",
            {"scenario_id": self.scenario_id, "fiscal_year": self.fiscal_year, "docstatus": ["in", [0, 1]]},
            ["name", "docstatus"], as_dict=True, order_by="name asc", for_update=True)
        for row in rows:
            if row.name == self.name:
                continue
            frappe.throw(
                _("Current Forecast {0} is already the {1} declaration for {2} FY{3}. A scenario and year hold "
                  "one live declaration: work in it, or cancel it and amend it.").format(
                    row.name, states[int(row.docstatus)], self.scenario_id, self.fiscal_year),
                frappe.ValidationError)

    def before_submit(self):
        """Submit is the approval. The declaration takes a copy of the round's
        ``input_fingerprint`` (BUD-11-3): the hash of the inputs approved in
        the round declared HERE, frozen by the submit, so a reader can
        recompute the Cycle's and report a change since the declaration. Set
        in before_submit so it lands with the approval and nothing saves in
        on_submit. A Cycle with no stamp (approved before S4 K8) is refused by
        name: a blank copy would never match anything. The Cycle row is read
        FOR UPDATE (``_cycle_row``) and judged again: a Cycle cancelled since
        the draft was saved is refused here, never declared. The approver
        and the time are stamped too: the submitter approves, whoever
        drafted the document."""
        cycle = self._cycle_row(for_update=True)
        self._refuse_bad_cycle(cycle)
        if not cycle.input_fingerprint:
            frappe.throw(
                _("Budget Cycle {0} has no input fingerprint stamped (it was approved before fingerprints "
                  "were). Cancel and amend the Cycle so Approve stamps one, then declare it.").format(
                    self.budget_cycle),
                frappe.ValidationError)
        self.input_fingerprint = cycle.input_fingerprint
        self.approved_by = frappe.session.user
        self.approved_on = now_datetime()

    def on_submit(self):
        """Declared: the row enters the warehouse, after the commit."""
        self._resync()

    def before_cancel(self):
        """Cancel only while the year has an open period (the Budget Cycle
        rule, BUD-9-3): a fully closed year's forecast is history. Nothing
        changes before the gate. No workflow ships for this doctype; the
        shared helper sets a docstatus-2 state only when a site has installed
        one of its own. A cancelled declaration leaves the year with no
        current round (the warehouse WARNs, the readers read nothing) and
        stays as the audit trail; a correction is an amendment."""
        from konsol.period_status import assert_year_not_closed

        assert_year_not_closed(self.fiscal_year, _("cancel Current Forecast {0}").format(self.name))
        set_cancelled_state(self)

    def on_cancel(self):
        """Undeclared again: the row leaves the warehouse, after the commit."""
        self._resync()

    def _resync(self):
        """Queue the sync once per transaction, after the commit (konsol#124).
        The sync replaces the whole table with the submitted declarations, so
        one covers every declaration in the transaction; a draft save queues
        nothing, because a draft is not in the table either way. Imported
        here: the controller is loaded under test without clickhouse.py."""
        from konsol.clickhouse import sync_doctype_after_commit

        sync_doctype_after_commit(self.doctype, self.CH_TABLE, self.CH_FIELD_MAP)

    def autoname(self):
        """``CFC-<scenario>-<year>-<sha8>`` (budget_grain.digest_name:
        collision-safe, length-capped; never ``format:{#####}``, #535), or
        the first free ``<digest>-N``: a cancelled declaration keeps its name
        as the audit trail, and its amendment must not collide with it (the
        Budget Cycle pattern, #524)."""
        base = digest_name("CFC", [self.scenario_id, self.fiscal_year])
        name, n = base, 0
        while frappe.db.exists("Current Forecast", name):
            n += 1
            name = f"{base}-{n}"
        self.name = name

    def set_new_name(self, *args, **kwargs):
        """The declaration owns its name under any site naming setting: Frappe
        v16 names an amendment ``<base>-<n+1>`` before the controller runs,
        without checking that the name is free (budget_cycle.py's
        set_new_name has the trace). Once Frappe has named it, autoname names
        it again as the first free suffix; a name the caller passes, or a
        document already named, is left alone."""
        force = kwargs.get("force", args[0] if args else False)
        set_name = kwargs.get("set_name", args[1] if len(args) > 1 else None)
        named = self.flags.name_set and not force
        super().set_new_name(*args, **kwargs)
        if not named and not set_name:
            self.autoname()


def on_doctype_update():
    """The index validate's locking read runs on: (scenario, year, docstatus),
    equality on the first two and IN on the last. Its own index, not
    budget_intake.LOCK_INDEXES (that map is the intake's reads). add_index
    checks first, so this is idempotent."""
    frappe.db.add_index("Current Forecast", list(LIVE_INDEX), index_name=LIVE_INDEX_NAME)

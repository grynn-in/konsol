"""Budget Annual Input — a top-down annual figure, spread into months by profile.

konsolidat#146: this was `seeds/budget_annual_input.csv`, the last seed the dbt
project owned. Like the ISO currency and fiscal-calendar lists it had no second
writer, but budget figures are not something a transform repo should hold.

konsol already owned the *bottom-up* path — Budget Cycle → Budget Entry →
Budget Entry Line, written through to `epm_raw.budget_entries` (konsol#348
S2-I). This is the other half: one annual number plus a Spread Profile, which
`gold_spread_budget` turns into twelve monthly rows.

S3 K6 (QB4 a): the row names its Budget Cycle. A forecast pair holds one live
Cycle per ROUND (K3), so an annual figure says which round it is for, and
everything that used to be judged by the (scenario, year) pair is judged by
the row's Cycle: the writable guard, the delete, the unique grain, the
one-writer check in both directions and the warehouse sync filter. The pair
is still locked first (QB9 a, name order): the rounds of one pair serialise.
"""
import frappe
from frappe.model.document import Document

from konsol.clickhouse import sync_doctype_after_commit

#: The columns every annual budget row carries, whatever the site declares.
FIXED_FIELD_MAP = {
    "scenario_id": "scenario_id",
    "data_area_id": "data_area_id",
    "fiscal_year": "fiscal_year",
    # Budget S3 (K6/B3k, QB4 a): the row's Budget Cycle, the round for a forecast
    "cycle": "cycle",
    "main_account": "main_account",
    # Budget S2 (A7): both required on the doctype (A5)
    "layer": "layer",
    "currency": "currency",
    "annual_amount": "annual_amount",
    "spread_profile_id": "spread_profile_id",
    "submitted_by": "submitted_by",
}


def budget_dimension_fields():
    """The budget dimension fields this doctype carries, in name order.

    konsol#287: they are the Published Dimensions ticked in_budget, provisioned
    as Custom Fields by schema_apply._sync_budget_custom_fields (as on Budget
    Line), not two fixed fields named after one customer's cost centres. Read
    off the cached meta rather than the Dimension registry: what the doctype
    can hold is what it can be asked about, a dimension declared but not yet
    provisioned is left out instead of named in a query on a column the table
    lacks, and reconcile reads this on discovery, where a query that could fail
    would stop every other table's sync.
    """
    meta = frappe.get_meta("Budget Annual Input")
    # Data only, the type the dimension sync creates: a dim_-named Section
    # Break or a hand-made field of another type is not a budget dimension.
    return sorted(f.fieldname for f in meta.fields
                  if (f.fieldname or "").startswith("dim_") and f.fieldtype == "Data")


class _FieldMap:
    """CH_FIELD_MAP, worked out when it is read: the fixed columns plus the
    site's budget dimensions. reconcile_all reads it off the class, so it
    cannot be a dict fixed at import."""

    def __get__(self, instance, owner):
        return {**FIXED_FIELD_MAP, **{d: d for d in budget_dimension_fields()}}


#: The filter for "no row qualifies". A name is never NULL or '', so this
#: matches nothing. Never ``["in", []]``, and never an empty dict:
#: resolve_sync_filters treats a falsy declaration as none and syncs every row.
_MATCH_NOTHING = {"name": ["is", "not set"]}


def approved_annual_input_names():
    """Names of the annual rows whose Budget Cycle is approved (docstatus 1).

    BUD-4, then S3 K6 (QB4 a): the row names its Cycle, so the rows of an
    approved Cycle are read by that link. A row of another Cycle of the same
    pair (a cancelled original, a draft round) does not qualify, nor does a
    row whose Cycle is gone. A draft, cancelled or absent cycle approves
    nothing.
    """
    approved = sorted(c["name"] for c in frappe.get_all(
        "Budget Cycle", filters={"docstatus": 1}, fields=["name"], order_by="name asc", limit_page_length=0))
    if not approved:
        return []
    rows = frappe.get_all("Budget Annual Input", filters={"cycle": ["in", approved]}, fields=["name"],
                          order_by="name asc", limit_page_length=0)
    return sorted(r["name"] for r in rows)


class _SyncFilters:
    """CH_SYNC_FILTERS, worked out when read (BUD-4).

    Budget Annual Input is not submittable, so without a declaration
    resolve_sync_filters returns None and every row reaches the warehouse,
    whatever its cycle's state. Both write paths read this through
    resolve_sync_filters: the per-save sync (on_update, after_delete) and
    reconcile_all. Each is a TRUNCATE+INSERT, so a row that leaves the set
    leaves the warehouse at the next sync.
    """

    def __get__(self, instance, owner):
        names = approved_annual_input_names()
        return {"name": ["in", names]} if names else dict(_MATCH_NOTHING)


class BudgetAnnualInput(Document):
    CH_TABLE = "epm_gold.budget_annual_input"
    CH_FIELD_MAP = _FieldMap()
    CH_SYNC_FILTERS = _SyncFilters()

    def validate(self):
        # The copy the Planning sidebar filters on, taken under a share lock
        # so it cannot race a Scenario type change (review of #460).
        from konsol.epm.doctype.scenario.scenario import locked_scenario_type

        cycle = self._own_cycle(self._lock_cycles())
        self._intake_check()
        self._validate_main_account_published()
        self._validate_spread_profile()
        self.scenario_type = locked_scenario_type(self.scenario_id)
        self._stamp_submitter()
        self._guard_cycle_writable(cycle)
        self._validate_unique_grain()
        self._refuse_second_writer()

    def check_if_latest(self, *args, **kwargs):
        """Frappe's check reads this row FOR UPDATE (load_doc_before_save).
        The Cycle is locked first, the order every budget writer keeps."""
        self._lock_cycles()
        return super().check_if_latest(*args, **kwargs)

    def _lock_cycles(self):
        """Lock the Budget Cycle rows of (scenario, year) (Q16-c, QB9 a) and
        return them as the lock read them ({name, docstatus, workflow_state,
        as_of_date}): a Budget Entry writer holds the same lock while it
        checks this doctype, so the two one-writer checks cannot both pass,
        and two Annual Inputs on one grain run _validate_unique_grain one
        after the other. validate takes it first; the rows come back in name
        order, the order every budget writer locks them in.

        A row without a Cycle is refused before anything is read (QB4 a: the
        Link is required, and nothing below can be judged without it). A pair
        with no Budget Cycle is refused by name (20-b): there would be nothing
        to lock, and a budget write never creates a Cycle (Q6-b). Install and
        migrate skip both, as lock_cycles does."""
        if frappe.flags.in_install or frappe.flags.in_migrate:
            return []
        if not self.cycle:
            frappe.throw("Budget Cycle is required: an annual figure belongs to a Cycle (for a forecast, "
                         "the round) of its scenario and year.", frappe.ValidationError)
        if not (self.scenario_id and self.fiscal_year):
            return []
        from konsol import budget_intake

        cycles = budget_intake.lock_cycles(self.scenario_id, self.fiscal_year)
        if not cycles:
            frappe.throw(
                f"No Budget Cycle exists for {self.scenario_id} {self.fiscal_year}. "
                "Create the Cycle before entering annual figures.",
                frappe.ValidationError,
            )
        return cycles

    def _own_cycle(self, cycles):
        """The row's Cycle among the pair's locked rows, or None in install
        and migrate. A Cycle that is not one of the pair's is refused naming
        the pair it belongs to (or that it does not exist): the row's
        scenario and year and its Cycle must agree, and for a forecast the
        Cycle is the round."""
        if frappe.flags.in_install or frappe.flags.in_migrate:
            return None
        for cycle in cycles:
            if cycle["name"] == self.cycle:
                return cycle
        theirs = frappe.db.get_value("Budget Cycle", self.cycle, ["scenario_id", "fiscal_year"])
        if not theirs:
            frappe.throw(f"Budget Cycle {self.cycle} does not exist.", frappe.ValidationError)
        frappe.throw(
            f"Budget Cycle {self.cycle} is a Cycle of {theirs[0]} {theirs[1]}, not of this figure's "
            f"{self.scenario_id} {self.fiscal_year}. Pick a Cycle of {self.scenario_id} {self.fiscal_year}.",
            frappe.ValidationError,
        )

    def _refuse_second_writer(self):
        """A grain takes its numbers from one writer (Q16-c): refused when a
        Budget Entry line of this grain exists, in any period. After the Cycle
        lock, with a locking read. Skipped in install and migrate."""
        if frappe.flags.in_install or frappe.flags.in_migrate:
            return
        from konsol import budget_intake

        budget_intake.assert_no_entry_lines(self._intake_row(), name=self.name)

    def before_cancel(self):
        self._guard_cycle_writable(self._own_cycle(self._lock_cycles()))

    def on_trash(self):
        """The lock has to hold on DELETE too, not just on edit.

        after_delete republishes the table immediately, so without this a locked
        cycle could still be changed by deleting a row instead of editing one —
        the same hole through the budget lock, via the other verb. on_trash is
        the right hook for a REFUSAL (it runs before the row is gone, so the
        throw prevents the delete); the publish stays in after_delete.

        The write-access check runs here too: delete is a write, and a user
        granted one entity must not delete another's annual figure.

        The Cycle is locked here too, so a delete cannot slip past a Cycle
        approval that is committing. Frappe has locked this row already, so
        the Cycle lock is taken NOWAIT (lock_cycles(wait=False)): a Cycle
        another writer holds refuses the delete at once instead of waiting
        while this row is held. The row's own Cycle is judged (S3 K6), not
        the pair's others; a row whose Cycle is gone guards nothing: an
        orphan figure may go, as an orphan Budget Entry may. A row of a
        cancelled Cycle may go too (19-b: it is out of the warehouse).
        """
        if not (frappe.flags.in_install or frappe.flags.in_migrate):
            from konsol import budget_intake

            budget_intake.assert_annual_access(self._intake_row())
            if self.cycle and self.scenario_id and self.fiscal_year:
                cycles = budget_intake.lock_cycles(self.scenario_id, self.fiscal_year, wait=False)
                own = next((c for c in cycles if c["name"] == self.cycle), None)
                if own:
                    self._guard_cycle_writable(own, deleting=True)

    def _validate_main_account_published(self):
        """The warehouse chart reads Published Main Accounts only, so a row on
        a Draft or Inactive account would never map. The Link accepts any
        status; refuse it here. Skipped during install/migrate."""
        if frappe.flags.in_install or frappe.flags.in_migrate or not self.main_account:
            return
        status = frappe.db.get_value("Main Account", self.main_account, "status")
        if status != "Published":
            frappe.throw(
                f"Main Account {self.main_account} is {status}, not Published. "
                f"The warehouse chart reads Published accounts only, so this "
                f"budget row would never map. Publish the account or pick a "
                f"published one."
            )

    def _validate_spread_profile(self):
        """spread_profile_id is a Spread Profile's profile_id (konsol#525).

        gold_spread_budget inner-joins the profile on profile_id, so a value
        that matches none (a row name such as SP-EVEN-P1 instead of EVEN, a
        typo, a profile never created) drops the annual figure from the budget
        fact without a word. Refused here, naming the value and the valid keys.
        The match is exact and case-sensitive, as the warehouse join is: the
        keys are read and compared here, not matched by a database collation.
        Skipped during install/migrate, as the other checks are.
        """
        if frappe.flags.in_install or frappe.flags.in_migrate:
            return
        keys = sorted({r.get("profile_id") for r in frappe.get_all(
            "Spread Profile", fields=["profile_id"], limit_page_length=0) if r.get("profile_id")})
        if self.spread_profile_id in keys:
            return
        valid = ", ".join(keys) if keys else "none: create a Spread Profile first"
        frappe.throw(
            f"Spread Profile {self.spread_profile_id!r} is not a Spread Profile's profile_id, so the "
            f"warehouse would drop this annual figure from the budget. Use a profile_id (not a row name "
            f"like SP-EVEN-P1). Valid profile_ids: {valid}.",
            frappe.ValidationError,
        )

    def _validate_unique_grain(self):
        """One annual figure per (scenario, entity, year, layer, account,
        dimensions).

        The dimensions are the site's declared budget dimensions (konsol#287).
        The layer is part of the grain: gold_spread_budget keeps each layer's
        rows apart (konsolidat#300), so a challenge figure beside the base
        figure for one account is a second layer, not a duplicate.

        gold_spread_budget inner-joins the profile and unions the result with no
        dedup and no aggregation, so two rows at the same grain produce two sets
        of twelve monthly rows and the budget silently doubles. The CSV this
        replaced had implicit uniqueness; autoname is `hash`, so nothing here
        does. Budget Entry protects its own grain the same way, via digest_name.
        """
        # The Cycle is part of the grain (S3 K6, QB4 a): two rounds may each
        # give the account its annual figure. scenario_id and fiscal_year
        # stay in the filter, redundant beside the Cycle, so the locking read
        # runs on the lock index (budget_intake.LOCK_INDEXES).
        grain = {
            "scenario_id": self.scenario_id,
            "data_area_id": self.data_area_id,
            "fiscal_year": self.fiscal_year,
            "cycle": self.cycle,
            "layer": self.layer,
            "main_account": self.main_account,
            "name": ["!=", self.name],
        }
        # A blank dimension matches NULL as well as '': a dimension field added
        # after rows exist is NULL on every one of them, so an '' filter would
        # miss the very row this would duplicate (the F3 trap).
        grain.update({d: self.get(d) or ["is", "not set"]
                      for d in budget_dimension_fields()})
        # A locking read after the Cycle lock, on the lock index's columns: a
        # plain read serves this transaction's snapshot (REPEATABLE READ) and
        # misses a row another session committed while this one waited.
        found = frappe.db.get_values("Budget Annual Input", grain, ["name"], as_dict=True,
                                     order_by="name asc", for_update=True)
        dupe = found[0].name if found else None
        if dupe:
            frappe.throw(
                f"An annual budget row already exists for {self.scenario_id} / "
                f"{self.data_area_id} / {self.fiscal_year} / Cycle {self.cycle} / layer {self.layer} / "
                f"{self.main_account} ({dupe}). Two rows at one grain double the spread budget."
            )

    def _intake_row(self):
        row = {f: self.get(f) for f in (
            "scenario_id", "data_area_id", "fiscal_year", "cycle", "main_account", "layer", "currency")}
        row.update({d: self.get(d) for d in budget_dimension_fields()})
        return row

    def _intake_check(self):
        """Every budget writer goes through konsol.budget_intake: the entity,
        account and dimension access check (konsol#413), the declared layer and
        its writer role, a plan scenario, and the entity's functional currency.
        Skipped during install/migrate only: Desk Data Import sets in_import,
        and an importing user must not bypass the check."""
        if frappe.flags.in_install or frappe.flags.in_migrate:
            return
        from konsol import budget_intake

        budget_intake.annual_check(self._intake_row())

    def _stamp_submitter(self):
        """submitted_by is an audit field, not free text."""
        if not self.submitted_by:
            self.submitted_by = frappe.session.user

    def _guard_cycle_writable(self, cycle, *, deleting=False):
        """An annual figure is edited, cancelled or deleted only while its
        Cycle takes budget writes (W4, the V2 rule): refused under an approved
        Cycle, and under a draft past its workflow's first state (Pending
        Approval as installed), naming the Cycle and its state. A cancelled
        Cycle refuses a save too, naming the amendment as the way (a figure
        written into a cancelled round is lost), but not a delete (19-b: its
        rows are out of the warehouse and may go).

        This is the top-down path into the same model (gold_spread_budget) as
        the Budget Entries, so an ungated write here would be a way past the
        Cycle's approval. `cycle` is the row's own Cycle as
        budget_intake.lock_cycles read it, under the lock (S3 K6: the row
        links it, the pair's other Cycles are other rounds): a Cycle approved
        or sent for approval while this write waited is seen, which a plain
        read of the snapshot would miss. Which Cycle refuses is
        budget_bulk_model.cycle_closed_to_writes. Skipped during
        install/migrate only (`cycle` is None then): a Data Import is a
        user's write and meets the rule. Fixtures and patches load against
        whatever state the site is in.
        """
        if frappe.flags.in_install or frappe.flags.in_migrate or cycle is None:
            return
        from konsol import budget_intake
        from konsol.budget_bulk_model import cycle_closed_to_writes, cycle_pending_refusal

        if int(cycle["docstatus"]) == 2:
            if deleting:
                return
            frappe.throw(
                f"Budget Cycle {cycle['name']} ({self.scenario_id} {self.fiscal_year}) is cancelled: an annual "
                "figure is written only into a draft Cycle. Amend the Cycle and enter the figure under the "
                "amendment.", frappe.ValidationError)
        workflow = budget_intake.workflow_for([cycle])
        if not cycle_closed_to_writes(cycle, workflow):
            return
        frappe.throw(cycle_pending_refusal(cycle, workflow) or (
            f"Budget Cycle {cycle['name']} ({self.scenario_id} {self.fiscal_year}) is approved: annual "
            "figures change only while the Cycle is a draft in its workflow's first state. Cancel the "
            "Cycle and amend it to change them."), frappe.ValidationError)

    def on_update(self):
        queue_sync()

    def after_delete(self):
        """after_delete, NOT on_trash.

        ``sync_doctype`` re-sends the whole table from ``frappe.get_all``, and
        on_trash runs BEFORE the row is removed — so a delete would re-publish
        the row it just deleted and leave it live in the warehouse until
        something else resynced this doctype. Same reasoning as
        GovernedReferenceDocument.after_delete and the Connector registry
        (test_connector_registry, test_dimension_mapping).
        """
        queue_sync()


def queue_sync():
    """Queue the whole-table sync of Budget Annual Input, once per transaction,
    after the commit. A save, a delete, and a Budget Cycle submit or cancel
    (which changes which rows qualify under CH_SYNC_FILTERS) all call this, so
    they share one key and one sync."""
    sync_doctype_after_commit("Budget Annual Input", BudgetAnnualInput.CH_TABLE, BudgetAnnualInput.CH_FIELD_MAP)


def on_doctype_update():
    """The index this doctype's budget locking read runs on (PR #522 review;
    konsol.budget_intake.LOCK_INDEXES)."""
    from konsol.budget_intake import add_lock_index

    add_lock_index("Budget Annual Input")

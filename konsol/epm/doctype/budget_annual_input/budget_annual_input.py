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

from konsol.clickhouse import sync_doctype_after_commit

#: The columns every annual budget row carries, whatever the site declares.
FIXED_FIELD_MAP = {
    "scenario_id": "scenario_id",
    "data_area_id": "data_area_id",
    "fiscal_year": "fiscal_year",
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

    BUD-4: the cycle is keyed on (scenario_id, fiscal_year), as this row is.
    The pair is matched, not each half: approving (S1, 2026) and (S2, 2025)
    must not approve (S1, 2025). A draft, cancelled or absent cycle approves
    nothing.
    """
    approved = {
        (c.get("scenario_id"), str(c.get("fiscal_year")))
        for c in frappe.get_all(
            "Budget Cycle", filters={"docstatus": 1},
            fields=["scenario_id", "fiscal_year"], limit_page_length=0)
    }
    if not approved:
        return []
    rows = frappe.get_all(
        "Budget Annual Input",
        filters={"scenario_id": ["in", sorted({s for s, _ in approved})]},
        fields=["name", "scenario_id", "fiscal_year"], limit_page_length=0)
    return sorted(r["name"] for r in rows
                  if (r.get("scenario_id"), str(r.get("fiscal_year"))) in approved)


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

        self._lock_cycles()
        self._intake_check()
        self._validate_main_account_published()
        self.scenario_type = locked_scenario_type(self.scenario_id)
        self._stamp_submitter()
        self._guard_cycle_locked()
        self._validate_unique_grain()
        self._refuse_second_writer()

    def check_if_latest(self, *args, **kwargs):
        """Frappe's check reads this row FOR UPDATE (load_doc_before_save).
        The Cycle is locked first, the order every budget writer keeps."""
        self._lock_cycles()
        return super().check_if_latest(*args, **kwargs)

    def _lock_cycles(self):
        """Lock the Budget Cycle rows of (scenario, year) (Q16-c): a Budget
        Entry writer holds the same lock while it checks this doctype, so the
        two one-writer checks cannot both pass, and two Annual Inputs on one
        grain run _validate_unique_grain one after the other. validate takes
        it first.

        A pair with no Budget Cycle is refused by name (20-b): there would be
        nothing to lock, and a budget write never creates a Cycle (Q6-b).
        Install and migrate skip both, as lock_cycles does."""
        if not (self.scenario_id and self.fiscal_year):
            return
        from konsol import budget_intake

        names = budget_intake.lock_cycles(self.scenario_id, self.fiscal_year)
        if not names and not (frappe.flags.in_install or frappe.flags.in_migrate):
            frappe.throw(
                f"No Budget Cycle exists for {self.scenario_id} {self.fiscal_year}. "
                "Create the Cycle before entering annual figures.",
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
        self._guard_cycle_locked()

    def on_trash(self):
        """The lock has to hold on DELETE too, not just on edit.

        after_delete republishes the table immediately, so without this a locked
        cycle could still be changed by deleting a row instead of editing one —
        the same hole through the budget lock, via the other verb. on_trash is
        the right hook for a REFUSAL (it runs before the row is gone, so the
        throw prevents the delete); the publish stays in after_delete.

        The write-access check runs here too: delete is a write, and a user
        granted one entity must not delete another's annual figure.
        """
        if not (frappe.flags.in_install or frappe.flags.in_migrate):
            from konsol import budget_intake

            budget_intake.assert_annual_access(self._intake_row())
        self._guard_cycle_locked()

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
        does. Budget Sheet protects its own grain the same way, via digest_name.
        """
        grain = {
            "scenario_id": self.scenario_id,
            "data_area_id": self.data_area_id,
            "fiscal_year": self.fiscal_year,
            "layer": self.layer,
            "main_account": self.main_account,
            "name": ["!=", self.name],
        }
        # A blank dimension matches NULL as well as '': a dimension field added
        # after rows exist is NULL on every one of them, so an '' filter would
        # miss the very row this would duplicate (the F3 trap).
        grain.update({d: self.get(d) or ["is", "not set"]
                      for d in budget_dimension_fields()})
        dupe = frappe.db.exists("Budget Annual Input", grain)
        if dupe:
            frappe.throw(
                f"An annual budget row already exists for {self.scenario_id} / "
                f"{self.data_area_id} / {self.fiscal_year} / layer {self.layer} / {self.main_account} "
                f"({dupe}). Two rows at one grain double the spread budget."
            )

    def _intake_row(self):
        row = {f: self.get(f) for f in (
            "scenario_id", "data_area_id", "fiscal_year", "main_account", "layer", "currency")}
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

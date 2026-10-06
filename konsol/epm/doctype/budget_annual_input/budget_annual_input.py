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
    return sorted(f.fieldname for f in meta.fields
                  if (f.fieldname or "").startswith("dim_"))


class _FieldMap:
    """CH_FIELD_MAP, worked out when it is read: the fixed columns plus the
    site's budget dimensions. reconcile_all reads it off the class, so it
    cannot be a dict fixed at import."""

    def __get__(self, instance, owner):
        return {**FIXED_FIELD_MAP, **{d: d for d in budget_dimension_fields()}}


class BudgetAnnualInput(Document):
    CH_TABLE = "epm_gold.budget_annual_input"
    CH_FIELD_MAP = _FieldMap()

    def validate(self):
        self._stamp_submitter()
        self._guard_cycle_locked()
        self._validate_unique_grain()

    def before_cancel(self):
        self._guard_cycle_locked()

    def on_trash(self):
        """The lock has to hold on DELETE too, not just on edit.

        after_delete republishes the table immediately, so without this a locked
        cycle could still be changed by deleting a row instead of editing one —
        the same hole through the budget lock, via the other verb. on_trash is
        the right hook for a REFUSAL (it runs before the row is gone, so the
        throw prevents the delete); the publish stays in after_delete.
        """
        self._guard_cycle_locked()

    def _validate_unique_grain(self):
        """One annual figure per (scenario, entity, year, account, dimensions).

        The dimensions are the site's declared budget dimensions (konsol#287).

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
                f"{self.data_area_id} / {self.fiscal_year} / {self.main_account} "
                f"({dupe}). Two rows at one grain double the spread budget."
            )

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
        sync_doctype_after_commit(self.doctype, self.CH_TABLE, self.CH_FIELD_MAP)

    def after_delete(self):
        """after_delete, NOT on_trash.

        ``sync_doctype`` re-sends the whole table from ``frappe.get_all``, and
        on_trash runs BEFORE the row is removed — so a delete would re-publish
        the row it just deleted and leave it live in the warehouse until
        something else resynced this doctype. Same reasoning as
        GovernedReferenceDocument.after_delete and the Connector registry
        (test_connector_registry, test_dimension_mapping).
        """
        sync_doctype_after_commit(self.doctype, self.CH_TABLE, self.CH_FIELD_MAP)

"""Budget Entry Line: one account's debit and credit in a Budget Entry (Q1-b2).

Each line is one row of ``epm_raw.budget_entries``. A raw row needs the
entry's header (cycle, scenario, layer, entity, year, period, currency, basis)
beside the line's own account and amounts. ``sync_doctype`` reads this child
table with ``frappe.get_all``, and on Frappe v16 a child query cannot reach
its parent's fields: ``parent.cycle`` is refused, and ```tabBudget Entry`.x``
joins the parent as if it were a child of the line (measured on 16.55.2,
13 Nov 2026). So the line carries a read-only, hidden copy of the header,
which the entry writes onto every line on each save (``stamp_header``). Lines
are saved only through their entry, so the copy cannot drift from it; nothing
is typed twice.
"""
import frappe
from frappe.model.document import Document

from konsol.clickhouse import sync_doctype_after_commit

DOCTYPE = "Budget Entry Line"

#: The entry's fields copied onto each line, under the same names.
HEADER_FIELDS = (
    "cycle",
    "scenario_id",
    "layer",
    "data_area_id",
    "fiscal_year",
    "fiscal_period",
    "currency",
    "amount_basis",
)

#: epm_raw.budget_entries' fixed columns -> the line's fields. The site's
#: budget dimensions follow (see _FieldMap).
FIXED_FIELD_MAP = {
    "entry_name": "parent",
    "cycle": "cycle",
    "scenario_id": "scenario_id",
    "layer": "layer",
    "data_area_id": "data_area_id",
    "fiscal_year": "fiscal_year",
    "fiscal_period": "fiscal_period",
    "main_account": "main_account",
    "debit": "debit",
    "credit": "credit",
    "currency": "currency",
    "amount_basis": "amount_basis",
}


def budget_dimension_fields():
    """The budget dimension fields on this doctype, in name order.

    They are the Published in_budget Dimensions, provisioned as Custom Fields
    by schema_apply (S2-I4). Read off the cached meta, as Budget Annual Input
    does: a dimension declared but not yet provisioned is left out rather than
    named in a query on a column the table lacks.
    """
    meta = frappe.get_meta(DOCTYPE)
    return sorted(f.fieldname for f in meta.fields
                  if (f.fieldname or "").startswith("dim_") and f.fieldtype == "Data")


class _FieldMap:
    """CH_FIELD_MAP, worked out when read: the fixed columns plus the site's
    budget dimensions. reconcile_all reads it off the class, so it cannot be
    a dict fixed at import."""

    def __get__(self, instance, owner):
        return {**FIXED_FIELD_MAP, **{d: d for d in budget_dimension_fields()}}


class BudgetEntryLine(Document):
    CH_TABLE = "epm_raw.budget_entries"
    CH_FIELD_MAP = _FieldMap()
    # BUD-4: a line reaches the warehouse only while its Budget Cycle is
    # approved (docstatus 1). A child row's own docstatus follows Budget
    # Entry, which is not submittable, so it says nothing about approval.
    # Frappe resolves ``cycle.docstatus`` with a LEFT JOIN on the line's own
    # Link: a draft, cancelled, deleted or blank cycle matches nothing, and
    # there is no list of names that could be empty.
    CH_SYNC_FILTERS = {"cycle.docstatus": 1}


def stamp_header(entry):
    """Copy the entry's header onto each of its lines. The entry calls this
    in ``validate`` (S2-T1), so every save writes the header it holds."""
    for line in entry.get("lines") or []:
        for field in HEADER_FIELDS:
            line.set(field, entry.get(field))


def queue_sync():
    """Queue the whole-table sync of Budget Entry Line, once per transaction,
    after the commit. A Budget Cycle submit or cancel changes which lines
    qualify under CH_SYNC_FILTERS, so it calls this (S2-T2)."""
    sync_doctype_after_commit(DOCTYPE, BudgetEntryLine.CH_TABLE, BudgetEntryLine.CH_FIELD_MAP)


def on_doctype_update():
    """The index this doctype's budget locking read runs on (PR #522 review;
    konsol.budget_intake.LOCK_INDEXES)."""
    from konsol.budget_intake import add_lock_index

    add_lock_index("Budget Entry Line")

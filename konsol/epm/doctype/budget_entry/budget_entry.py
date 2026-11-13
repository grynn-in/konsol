"""Budget Entry: one (cycle, entity, layer, period) of budget lines (Q1-b2).

The entry holds the header; its Budget Entry Line rows hold the accounts and
amounts. The name is a digest of the key, so the same key twice is refused,
and the key fields are set once. Scenario and year are fetched from the
Cycle and never typed. Every save copies the header onto the lines
(``stamp_header``, T0), so each warehouse row carries it.

Not submittable, and no sync hook: the Cycle's submit and cancel sync the
lines (T2). The entry guards that itself: ``validate`` and ``on_trash`` read
the Cycle with a locking read (``assert_cycle_draft``) and refuse unless it is
a draft (docstatus 0). The lock makes a Cycle submit wait for an open write,
so the submit's after-commit sync includes it; a write that starts after the
submit sees docstatus 1 and is refused. Install and migrate skip the check.

``validate`` then judges the lines through konsol.budget_intake, the same
rules as every other budget writer (access, the layer's writer role,
currency, basis, period, account, scenario). A writer that has already judged
the rows it writes (the Excel cell, the bulk load) saves inside
``judged_by_writer()``, so the rows are not judged twice. An entry without
lines is judged on its Cycle only: there is no row to judge.
"""
from contextlib import contextmanager
from decimal import Decimal

import frappe
from frappe.model.document import Document

from konsol.epm.budget_grain import digest_name
from konsol.epm.doctype.budget_entry_line.budget_entry_line import (
    budget_dimension_fields, stamp_header,
)

#: The fields that make one entry: the name is their digest.
KEY_FIELDS = ("cycle", "data_area_id", "layer", "fiscal_period")

#: Docstatus words for a Cycle that is not a draft.
_CYCLE_STATE = {1: "approved", 2: "cancelled"}

#: frappe.flags key a writer sets (judged_by_writer) when it has judged its rows.
_JUDGED_FLAG = "budget_entry_judged"


class CycleNotDraftError(frappe.ValidationError):
    """A Budget Entry write under a Cycle that is approved or cancelled."""


def assert_cycle_draft(cycle, action="changed", missing_ok=False):
    """Lock the Cycle row (SELECT ... FOR UPDATE) and refuse unless it is a
    draft, naming the Cycle and its state. A locking read sees the latest
    committed docstatus (REPEATABLE READ would otherwise serve the snapshot)
    and holds the row until the transaction ends, so a Cycle submit waits for
    this write. Skipped in install and migrate. A writer calls it before it
    locks any entry, so every path takes the Cycle lock first."""
    if frappe.flags.in_install or frappe.flags.in_migrate:
        return
    rows = frappe.db.sql("SELECT docstatus FROM `tabBudget Cycle` WHERE name=%s FOR UPDATE", (cycle,))
    if not rows:
        if missing_ok:
            return
        frappe.throw(f"Budget Cycle {cycle} does not exist.", frappe.ValidationError)
    docstatus = int(rows[0][0])
    if docstatus != 0:
        state = _CYCLE_STATE.get(docstatus, f"docstatus {docstatus}")
        frappe.throw(f"Budget Cycle {cycle} is {state}: a Budget Entry is {action} only while its "
                     "Cycle is a draft.", CycleNotDraftError)


@contextmanager
def judged_by_writer():
    """Save Budget Entries in this scope without judging their lines again: the
    writer has run them through konsol.budget_intake itself. The Cycle check
    still runs."""
    was = frappe.flags.get(_JUDGED_FLAG)
    frappe.flags[_JUDGED_FLAG] = True
    try:
        yield
    finally:
        frappe.flags[_JUDGED_FLAG] = was


def entry_name(cycle, data_area_id, layer, fiscal_period):
    """The name of the entry for this key (its autoname): a writer looks an
    entry up by it, a locking read on the primary key."""
    return digest_name("BENT", [cycle, data_area_id, layer, fiscal_period])


def _amount(value):
    """A stored amount as the intake reads it: plain decimal notation."""
    return format(Decimal(repr(float(value or 0))), "f")


class BudgetEntry(Document):
    def autoname(self):
        self.name = entry_name(*(self.get(f) for f in KEY_FIELDS))

    def validate(self):
        assert_cycle_draft(self.cycle, "changed")
        self._fetch_from_cycle()
        self._refuse_duplicate_key()
        stamp_header(self)
        self._refuse_duplicate_lines()
        self._judge_lines()
        self._compute_totals()

    def on_trash(self):
        # A missing Cycle guards nothing: an orphan entry may go.
        assert_cycle_draft(self.cycle, "deleted", missing_ok=True)

    def _judge_lines(self):
        """The lines through konsol.budget_intake, as one budget table: a
        denial raises PermissionError, any other problem ValidationError."""
        lines = self.get("lines") or []
        if frappe.flags.get(_JUDGED_FLAG) or not lines:
            return
        from konsol import budget_intake

        dims = [d for d in budget_dimension_fields() if any(line.get(d) for line in lines)]
        header = ["scenario", "layer", "data_area_id", "fiscal_year", "fiscal_period",
                  "main_account", "debit", "credit", "currency"] + dims
        rows = [[self.scenario_id, self.layer, self.data_area_id, self.fiscal_year, self.fiscal_period,
                 line.get("main_account"), _amount(line.get("debit")), _amount(line.get("credit")),
                 self.currency or ""] + [line.get(d) or "" for d in dims]
                for line in lines]
        budget_intake.judge_one([header] + rows, form_basis=self.amount_basis or "")

    def _fetch_from_cycle(self):
        """Scenario and year are the Cycle's, whatever the entry held."""
        values = frappe.db.get_value("Budget Cycle", self.cycle, ["scenario_id", "fiscal_year"])
        if not values:
            frappe.throw(f"Budget Cycle {self.cycle} does not exist.", frappe.ValidationError)
        self.scenario_id, self.fiscal_year = values

    def _refuse_duplicate_key(self):
        """One entry per key, refused by name before the insert would fail on
        the digest name. Frappe sets a new entry's name before validate, so
        only a saved entry may exclude itself."""
        filters = {f: self.get(f) for f in KEY_FIELDS}
        if self.name and not self.is_new():
            filters["name"] = ["!=", self.name]
        other = frappe.db.get_value("Budget Entry", filters)
        if other:
            frappe.throw(
                f"Budget Entry {other} already holds Cycle {self.cycle}, entity "
                f"{self.data_area_id}, layer {self.layer}, period {self.fiscal_period}. "
                "Edit that entry instead.",
                frappe.DuplicateEntryError,
            )

    def _refuse_duplicate_lines(self):
        """``main_account`` plus the dim_* values is unique within an entry.
        A blank dimension and an unset one are the same value."""
        dims = budget_dimension_fields()
        seen = {}
        for line in self.get("lines") or []:
            key = (line.get("main_account"),) + tuple(line.get(d) or "" for d in dims)
            if key in seen:
                named = ", ".join(f"{d} {v}" for d, v in zip(dims, key[1:]) if v)
                frappe.throw(
                    f"Rows {seen[key]} and {line.idx} both hold account {key[0]}"
                    + (f" with {named}" if named else "")
                    + ". Keep one row per account and dimension combination.",
                    frappe.ValidationError,
                )
            seen[key] = line.idx

    def _compute_totals(self):
        """No balance rule: unequal debit and credit totals stand."""
        lines = self.get("lines") or []
        self.row_count = len(lines)
        self.total_debit = sum(line.get("debit") or 0 for line in lines)
        self.total_credit = sum(line.get("credit") or 0 for line in lines)

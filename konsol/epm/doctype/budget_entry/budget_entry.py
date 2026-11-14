"""Budget Entry: one (cycle, entity, layer, period) of budget lines (Q1-b2).

The entry holds the header; its Budget Entry Line rows hold the accounts and
amounts. The name is a digest of the key, so the same key twice is refused,
and the key fields are set once. Scenario and year are fetched from the
Cycle and never typed. Every save copies the header onto the lines
(``stamp_header``, T0), so each warehouse row carries it.

Not submittable, and no sync hook: the Cycle's submit and cancel sync the
lines (T2). The entry guards that itself with a locking read of the Cycle
(``assert_cycle_draft``): a save takes it in ``check_if_latest``, before
Frappe locks the entry, and again in ``validate``; a save is refused unless
the Cycle is a draft (docstatus 0). The lock makes a Cycle submit wait for an
open write, so the submit's after-commit sync includes it; a write that starts
after the submit sees docstatus 1 and is refused. A delete is refused under an
approved Cycle only (a cancelled Cycle's entries may go); Frappe locks the
entry before ``on_trash``, so the delete takes the Cycle lock NOWAIT and fails
fast instead of waiting while it holds the entry. Install and migrate skip
the check.

``validate`` then judges the lines through konsol.budget_intake, the same
rules as every other budget writer (access, the layer's writer role,
currency, basis, period, account, scenario). A writer that has already judged
the rows it writes (the Excel cell, the bulk load) saves inside
``judged_by_writer()``, so the rows are not judged twice. An entry without
lines is judged on its Cycle only: there is no row to judge. A line removed
on the Desk is judged for access and the layer's writer role.

The rules that span other records run on every save, whoever writes (the
writer's own judgement covers its rows, not the other entries), after the
Cycle lock and with locking reads (``_refuse_second_writer``): one amount
basis per (cycle, entity, layer) across all its entries (Q15-c,
``budget_intake.assert_one_basis``), and no line on a grain a Budget Annual
Input gives (Q16-c, ``budget_intake.assert_no_annual_input``). A Budget Upload that replaces a slice
whole saves inside ``replacing_entries(names)``: the slice's old entries are
rewritten in the same transaction, so their old basis is not a second one.
Install and migrate skip them, as they skip the Cycle lock.
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

#: frappe.flags key (replacing_entries): entries the current write rewrites.
_REPLACING_FLAG = "budget_entry_replacing"


class CycleNotDraftError(frappe.ValidationError):
    """A Budget Entry write under a Cycle that is approved or cancelled."""


class BudgetConflictError(frappe.ValidationError):
    """A budget_save period whose ``base_modified`` is stale: HTTP 409. Raised,
    not returned, so a multi-period or batch save never counts it as saved and
    the request (or the batch's savepoint) rolls back what it wrote before."""

    http_status_code = 409


def assert_cycle_draft(cycle, action="changed", *, missing_ok=False, wait=True, refuse=(1, 2)):
    """Lock the Cycle row (SELECT ... FOR UPDATE) and refuse a Cycle whose
    docstatus is in `refuse`, naming the Cycle and its state. A locking read
    sees the latest committed docstatus (REPEATABLE READ would otherwise serve
    the snapshot) and holds the row until the transaction ends, so a Cycle
    submit waits for this write. Skipped in install and migrate.

    Lock order: every budget writer locks the Cycle before any Budget Entry
    (the cell path, the load, and a save through check_if_latest). A delete
    cannot: Frappe locks the entry (NOWAIT) before on_trash and runs no hook
    earlier. So a delete passes ``wait=False``: it takes the Cycle lock NOWAIT
    and fails fast, rather than wait on the Cycle while holding the entry."""
    if frappe.flags.in_install or frappe.flags.in_migrate:
        return
    query = "SELECT docstatus FROM `tabBudget Cycle` WHERE name=%s FOR UPDATE"
    try:
        rows = frappe.db.sql(query if wait else query + " NOWAIT", (cycle,))
    except (frappe.QueryTimeoutError, frappe.QueryDeadlockError) as error:
        if wait:
            raise
        # Keep the type: a deadlock has already rolled the transaction back.
        frappe.throw(f"Budget Cycle {cycle} is being changed by another user, so this Budget Entry "
                     "cannot be deleted right now. Try again in a moment.", type(error))
    if not rows:
        if missing_ok:
            return
        frappe.throw(f"Budget Cycle {cycle} does not exist.", frappe.ValidationError)
    docstatus = int(rows[0][0])
    if docstatus in refuse:
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


@contextmanager
def replacing_entries(names):
    """Save Budget Entries in this scope as one write that rewrites the
    entries `names` (a Budget Upload replacing a slice whole): the basis rule
    does not count their old basis against the new one. The writer has judged
    that the new rows give one basis (budget_intake.plan)."""
    was = frappe.flags.get(_REPLACING_FLAG)
    frappe.flags[_REPLACING_FLAG] = frozenset(names)
    try:
        yield
    finally:
        frappe.flags[_REPLACING_FLAG] = was


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
        self._refuse_second_writer()
        self._compute_totals()

    def check_if_latest(self, *args, **kwargs):
        """Frappe's check reads this entry FOR UPDATE (load_doc_before_save).
        The Cycle is locked first, the order every budget writer keeps."""
        assert_cycle_draft(self.cycle, "changed")
        return super().check_if_latest(*args, **kwargs)

    def on_trash(self):
        # Frappe has locked this entry already: take the Cycle NOWAIT (see
        # assert_cycle_draft). Only an approved Cycle refuses: a cancelled
        # Cycle's entries are out of the warehouse and may go. A missing Cycle
        # guards nothing: an orphan entry may go.
        assert_cycle_draft(self.cycle, "deleted", missing_ok=True, wait=False, refuse=(1,))

    def _judge_lines(self):
        """The lines through konsol.budget_intake, as one budget table: a
        denial raises PermissionError, any other problem ValidationError. A
        line removed since the last save is a write to it too: its access and
        the layer's writer role are judged (assert_may_remove)."""
        if frappe.flags.get(_JUDGED_FLAG):
            return
        from konsol import budget_intake

        lines = self.get("lines") or []
        self._judge_removed_lines(budget_intake, lines)
        if not lines:
            return

        dims = [d for d in budget_dimension_fields() if any(line.get(d) for line in lines)]
        header = ["scenario", "layer", "data_area_id", "fiscal_year", "fiscal_period",
                  "main_account", "debit", "credit", "currency"] + dims
        rows = [[self.scenario_id, self.layer, self.data_area_id, self.fiscal_year, self.fiscal_period,
                 line.get("main_account"), _amount(line.get("debit")), _amount(line.get("credit")),
                 self.currency or ""] + [line.get(d) or "" for d in dims]
                for line in lines]
        budget_intake.judge_one([header] + rows, form_basis=self.amount_basis or "")

    def _refuse_second_writer(self):
        """The rules that span other records, for every writer: validate has
        locked the Cycle, so these locking reads see every committed write."""
        if frappe.flags.in_install or frappe.flags.in_migrate:
            return
        from konsol import budget_intake

        budget_intake.assert_one_basis(
            self.cycle, self.data_area_id, self.layer, self.amount_basis or "", name=self.name,
            replacing=frappe.flags.get(_REPLACING_FLAG) or ())
        # A whole-slice replacement (a Budget Upload) was judged by its plan.
        if frappe.flags.get(_REPLACING_FLAG) is None:
            budget_intake.assert_ytd_complete(
                self.cycle, self.data_area_id, self.layer, self.amount_basis or "",
                self.fiscal_period, name=self.name)
        dims = budget_dimension_fields()
        budget_intake.assert_no_annual_input(
            self.scenario_id, self.fiscal_year, self.data_area_id, self.layer,
            [{"main_account": line.get("main_account"), **{d: line.get(d) or "" for d in dims}}
             for line in self.get("lines") or []],
            entry=self.name, period=self.fiscal_period)

    def _judge_removed_lines(self, budget_intake, lines):
        before = self.get_doc_before_save()
        if not before:
            return
        kept = {line.get("name") for line in lines if line.get("name")}
        removed = [line for line in before.get("lines") or [] if line.get("name") not in kept]
        if removed:
            dims = budget_dimension_fields()
            budget_intake.assert_may_remove(self.data_area_id, self.layer, [
                {"main_account": line.get("main_account"), **{d: line.get(d) or "" for d in dims}}
                for line in removed])

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


def on_doctype_update():
    """The index this doctype's budget locking read runs on (PR #522 review;
    konsol.budget_intake.LOCK_INDEXES)."""
    from konsol.budget_intake import add_lock_index

    add_lock_index("Budget Entry")

"""Budget Entry: one (cycle, entity, layer, period) of budget lines (Q1-b2).

The entry holds the header; its Budget Entry Line rows hold the accounts and
amounts. The name is a digest of the key, so the same key twice is refused,
and the key fields are set once. Scenario and year are fetched from the
Cycle and never typed. Every save copies the header onto the lines
(``stamp_header``, T0), so each warehouse row carries it.

Not submittable, and no sync hook: an entry can change only under a Draft
Cycle, and the Cycle's submit and cancel sync the lines (T2).
"""
import frappe
from frappe.model.document import Document

from konsol.epm.budget_grain import digest_name
from konsol.epm.doctype.budget_entry_line.budget_entry_line import (
    budget_dimension_fields, stamp_header,
)

#: The fields that make one entry: the name is their digest.
KEY_FIELDS = ("cycle", "data_area_id", "layer", "fiscal_period")


class BudgetEntry(Document):
    def autoname(self):
        self.name = digest_name("BENT", [self.get(f) for f in KEY_FIELDS])

    def validate(self):
        self._fetch_from_cycle()
        self._refuse_duplicate_key()
        stamp_header(self)
        self._refuse_duplicate_lines()
        self._compute_totals()

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

"""Cost Allocation Driver: the statistic a cost pool is spread on (konsol#269).

The driver names a Dimension. That Dimension must be Published, so a rule set
can only spread on a dimension the rest of the model already recognises.

Phase 2b-1 (Q3 = 3B, Deepak Pai 13 Nov 2026; Q1, Q2 and Q7 = A, 27 Nov 2026)
adds where the driver's values come from:

* **Uploaded Statistic** (the default): a Cost Allocation Statistic per entity,
  scenario and year.
* **Fixed Weights**: shares held on the driver, one row per member and dated
  validity window. Windows of one member may not overlap; a run uses the row
  whose window covers its whole period. Scenario-agnostic: they are policy.
* **Account Set**: the scenario trial balance of the listed accounts (a group
  includes its descendants), by member of the driver's dimension, read at
  Compute. The driver declares the sign (As Booked / Reversed); a negative
  value refuses the run. The dimension must be In Budget, because the
  scenario trial balance carries budget dimensions only.

The child tables stay in Frappe: a run snapshots what it used in its weight
rows. A table filled for a source that does not read it is refused, never
ignored. Every problem is listed in one throw. The frappe reads are small
module helpers so tests can stub them.
"""
import datetime

import frappe
from frappe.model.document import Document

UPLOADED, FIXED, ACCOUNT_SET = "Uploaded Statistic", "Fixed Weights", "Account Set"
SOURCES = (UPLOADED, FIXED, ACCOUNT_SET)
VALUE_SIGNS = ("As Booked", "Reversed")
_SHOWN = 10
_PREFIX = "Cost Allocation Driver: "


def _dimension_status(dimension):
    return frappe.db.get_value("Dimension", dimension, "status")


def _dimension_in_budget(dimension):
    return frappe.db.get_value("Dimension", dimension, "in_budget") or 0


def _dimension_members(dimension):
    """Member values of a dimension (shared helper: no page cap, no user check)."""
    from konsol.cost_allocation_members import dimension_members

    return dimension_members(dimension)


def _listing(items):
    items = list(items)
    text = ", ".join(str(i) for i in items[:_SHOWN])
    return text + (f" and {len(items) - _SHOWN} more" if len(items) > _SHOWN else "")


def _date(value):
    """A date from what Frappe hands a Date field: a date, a datetime or an
    ISO string; None when blank."""
    if value in (None, ""):
        return None
    if isinstance(value, datetime.datetime):
        return value.date()
    if isinstance(value, datetime.date):
        return value
    return datetime.date.fromisoformat(str(value)[:10])


def _window(row):
    return f"{row.effective_from or '?'}–{row.effective_to or 'open'}"


def _table(doc, fieldname):
    return list(getattr(doc, fieldname, None) or [])


class CostAllocationDriver(Document):
    def validate(self):
        found = []
        status = _dimension_status(self.dimension)
        if status != "Published":
            found.append(
                f"Dimension {self.dimension} is {status or 'not found'}. "
                "A cost allocation driver needs a Published dimension.")
        source = getattr(self, "source", None)
        if source not in SOURCES:
            found.append(f"{_PREFIX}Source {source!r} is not one of {', '.join(SOURCES)}.")
        else:
            found += self._misplaced_problems(source)
            if source == FIXED:
                found += self._fixed_weight_problems()
            elif source == ACCOUNT_SET:
                found += self._account_set_problems()
        if found:
            frappe.throw("<br>".join(found))

    # -- a table filled for a source that does not read it ------------------

    def _misplaced_problems(self, source):
        found = []
        if source != FIXED and _table(self, "fixed_weights"):
            found.append(
                f"{_PREFIX}Fixed Weights rows are filled but Source is {source}; "
                "set Source to Fixed Weights, or switch to it and remove the rows.")
        if source != ACCOUNT_SET:
            if _table(self, "accounts"):
                found.append(
                    f"{_PREFIX}Account Set rows are filled but Source is {source}; "
                    "set Source to Account Set, or switch to it and remove the rows.")
            if getattr(self, "value_sign", None):
                found.append(
                    f"{_PREFIX}Value Sign is set but Source is {source}; "
                    "set Source to Account Set, or switch to it and clear Value Sign.")
        return found

    # -- Fixed Weights -------------------------------------------------------

    def _fixed_weight_problems(self):
        from konsol.cost_allocation_members import MemberReadError

        rows = _table(self, "fixed_weights")
        if not rows:
            return [f"{_PREFIX}Fixed Weights needs at least one row."]
        found = []
        try:
            members = _dimension_members(self.dimension)
        except MemberReadError as exc:
            found.append(f"{_PREFIX}{exc}")
            members = None
        if members is not None:
            unknown = sorted({r.member for r in rows if r.member not in members})
            if unknown:
                found.append(
                    f"{_PREFIX}not members of dimension {self.dimension}: {_listing(unknown)}.")
        negative = [f"{r.member} {_window(r)}" for r in rows if float(r.value or 0) < 0]
        if negative:
            found.append(f"{_PREFIX}values cannot be negative: {_listing(negative)}.")
        no_from = [r.member for r in rows if not r.effective_from]
        if no_from:
            found.append(f"{_PREFIX}Effective From is required: {_listing(no_from)}.")
        inverted = [f"{r.member} {_window(r)}" for r in rows
                    if r.effective_from and r.effective_to
                    and _date(r.effective_to) < _date(r.effective_from)]
        if inverted:
            found.append(f"{_PREFIX}Effective To is before Effective From: {_listing(inverted)}.")
        overlaps = self._overlapping_windows([r for r in rows if r.effective_from])
        if overlaps:
            found.append(f"{_PREFIX}overlapping windows: {_listing(overlaps)}.")
        return found

    @staticmethod
    def _overlapping_windows(rows):
        """'member a–b and c–d' for each pair of one member's windows that
        share a day. A blank Effective To is open-ended; windows are inclusive,
        so a window ending on the day another starts overlaps it."""
        by_member = {}
        for r in rows:
            by_member.setdefault(r.member, []).append(r)
        found = []
        for member, mine in sorted(by_member.items(), key=lambda kv: str(kv[0])):
            mine.sort(key=lambda r: _date(r.effective_from))
            for earlier, later in zip(mine, mine[1:]):
                end = _date(earlier.effective_to)
                if end is None or end >= _date(later.effective_from):
                    found.append(f"{member} {_window(earlier)} and {_window(later)}")
        return found

    # -- Account Set ---------------------------------------------------------

    def _account_set_problems(self):
        found = []
        rows = _table(self, "accounts")
        if not rows:
            found.append(f"{_PREFIX}Account Set needs at least one account.")
        else:
            seen, duplicate = set(), []
            for r in rows:
                if r.main_account in seen and r.main_account not in duplicate:
                    duplicate.append(r.main_account)
                seen.add(r.main_account)
            if duplicate:
                found.append(f"{_PREFIX}duplicate accounts: {_listing(duplicate)}.")
        sign = getattr(self, "value_sign", None)
        if sign not in VALUE_SIGNS:
            found.append(
                f"{_PREFIX}Value Sign ({' or '.join(VALUE_SIGNS)}) is required for an Account Set driver"
                + (f"; {sign!r} is neither." if sign else "."))
        if not _dimension_in_budget(self.dimension):
            found.append(
                f"{_PREFIX}Dimension {self.dimension} is not In Budget. An Account Set driver "
                "reads the scenario trial balance, which carries only dimensions marked "
                "Include in Budget (In Budget).")
        return found

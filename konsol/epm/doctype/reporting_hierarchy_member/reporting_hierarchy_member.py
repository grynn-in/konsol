"""Reporting Hierarchy Member — nodes in a management reporting tree.

A member row is one dated tranche of its code (konsol#220): effective_from to
effective_to, blank meaning open (OPEN_END in these checks, as in the
warehouse). A rename, a move or an end is a new row with the same code.
"""
from datetime import date, timedelta

import frappe
from frappe.model.document import Document

#: The warehouse's open end: the last day ClickHouse Date32 holds.
OPEN_END = "2299-12-31"
#: What an undated row means (the migrate patch uses the same day): always.
#: Also the first day Date32 holds.
_ALWAYS_FROM = "1900-01-01"


def _iso(value):
    return str(value)[:10] if value else ""


def _window(effective_from, effective_to):
    return _iso(effective_from) or _ALWAYS_FROM, _iso(effective_to) or OPEN_END


def _shown(day):
    return "open" if day == OPEN_END else day


def _span(start, end):
    return f"{start} to {_shown(end)}"


def _uncovered(start, end, windows):
    """The parts of [start, end] that no window in ``windows`` covers, as
    (from, to) ISO pairs in date order."""
    one_day = timedelta(days=1)
    cursor, last = date.fromisoformat(start), date.fromisoformat(end)
    gaps = []
    for a, b in sorted(windows):
        a, b = date.fromisoformat(a), date.fromisoformat(b)
        if b < cursor:
            continue
        if a > last:
            break
        if a > cursor:
            gaps.append((cursor, a - one_day))
        cursor = max(cursor, b + one_day)
        if cursor > last:
            break
    if cursor <= last:
        gaps.append((cursor, last))
    return [(g.isoformat(), h.isoformat()) for g, h in gaps]


class ReportingHierarchyMember(Document):

    def validate(self):
        self._validate_parent_scope()
        self._validate_window()
        self._validate_member_code()
        self._validate_parent_window()
        self._validate_no_cycles()

    def _validate_parent_scope(self):
        if not self.parent_member:
            return
        parent_h = frappe.db.get_value(
            "Reporting Hierarchy Member",
            self.parent_member,
            "reporting_hierarchy",
        )
        if parent_h != self.reporting_hierarchy:
            frappe.throw(
                "Parent member must belong to the same Reporting Hierarchy."
            )

    def _validate_member_code(self):
        if self.is_group and not self.member_code:
            if not self.member_label:
                return  # Frappe's mandatory check reports the missing label
            self.member_code = frappe.scrub(self.member_label).upper()[:140]
        if not self.member_code:
            frappe.throw(
                "Set a Member Code for this group node." if self.is_group
                else "Member Code is required for leaf nodes (Is Group = unchecked)."
            )
        # Group codes too: K.EPM's node argument and the warehouse rollup
        # find a node by its code and a date, so two rows of one code may
        # not overlap. Adjacent rows (a rename, a move) are the same node.
        start, end = self._window()
        others = frappe.get_all(
            "Reporting Hierarchy Member",
            filters={
                "reporting_hierarchy": self.reporting_hierarchy,
                "member_code": self.member_code,
                "name": ["!=", self.name],
            },
            fields=["name", "effective_from", "effective_to"],
        )
        for other in others:
            o_start, o_end = _window(other.effective_from, other.effective_to)
            if o_start <= end and start <= o_end:
                frappe.throw(
                    f"Member code '{self.member_code}' already has a row covering "
                    f"{_span(o_start, o_end)} ({other.name}). Dates of one code must "
                    "not overlap; end the old row the day before the new one starts."
                )

    def _validate_window(self):
        # The warehouse holds these as Date32, which can't store a day
        # outside 1900-01-01..2299-12-31.
        for label, value in (("Effective From", self.effective_from),
                             ("Effective To", self.effective_to)):
            day = _iso(value)
            if day and not (_ALWAYS_FROM <= day <= OPEN_END):
                frappe.throw(
                    f"{label} must be between {_ALWAYS_FROM} and {OPEN_END}."
                )
        start, end = self._window()
        if self.effective_to and end < start:
            frappe.throw("Effective To is before Effective From.")

    def _window(self):
        return _window(self.effective_from, self.effective_to)

    def _validate_parent_window(self):
        """The union of the parent CODE's tranches must cover this row's
        window: the parent may be renamed (a new row) without re-linking its
        children, but a child can't outlive or predate its parent."""
        if not self.parent_member:
            return
        parent_code = frappe.db.get_value(
            "Reporting Hierarchy Member", self.parent_member, "member_code"
        )
        tranches = frappe.get_all(
            "Reporting Hierarchy Member",
            filters={
                "reporting_hierarchy": self.reporting_hierarchy,
                "member_code": parent_code,
            },
            fields=["effective_from", "effective_to"],
        )
        start, end = self._window()
        gaps = _uncovered(
            start, end, [_window(t.effective_from, t.effective_to) for t in tranches]
        )
        if gaps:
            frappe.throw(
                f"{self.member_code} applies from {start} to {_shown(end)}, but its "
                f"parent {parent_code} does not cover "
                f"{', '.join(_span(a, b) for a, b in gaps)}."
            )

    def _validate_no_cycles(self):
        if not self.parent_member:
            return
        seen = {self.name}
        current = self.parent_member
        while current:
            if current in seen:
                frappe.throw("Parent chain forms a cycle — choose a different parent.")
            seen.add(current)
            current = frappe.db.get_value(
                "Reporting Hierarchy Member",
                current,
                "parent_member",
            )
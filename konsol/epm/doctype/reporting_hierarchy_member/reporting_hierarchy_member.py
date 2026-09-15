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
#: How far up the cycle walk goes before it stops.
_MAX_DEPTH = 50


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
        # Before the parent window: a row under its own code is refused as
        # such, not as a parent that doesn't cover it.
        self._validate_no_cycles()
        self._validate_parent_window()
        self._validate_children_keep_parent()

    def on_trash(self):
        self._check_children_of(self.member_code, None)

    def _validate_children_keep_parent(self):
        """A saved row whose code or window changes may leave a child of its
        OLD code uncovered: shortening one tranche, or moving it to another
        code, can open a gap no other tranche fills."""
        before = None if self.is_new() else self.get_doc_before_save()
        if not before:
            return
        old_code = before.member_code
        old_window = _window(before.effective_from, before.effective_to)
        if old_code == self.member_code:
            if old_window != self._window():
                self._check_children_of(old_code, self._window())
            return
        # A code change: children linked to THIS row move with it to the new
        # code; children linked to the old code's other tranches stay behind
        # and must be covered by those tranches alone.
        self._check_children_of(old_code, None, moving=False)
        self._check_children_of(self.member_code, self._window(), moving=True)

    def _check_children_of(self, code, replacement, moving=None):
        """Children must stay covered by ``code``'s tranches, with this row's
        window swapped for ``replacement`` (None: this row no longer counts).
        The table still holds this row's saved values here, in validate()
        and on_trash. ``moving`` picks the children: None, every member whose
        parent row has ``code`` in the table; False, those of them not linked
        to this row; True, those linked to this row (they follow it to its
        new code)."""
        rows = frappe.get_all(
            "Reporting Hierarchy Member",
            filters={"reporting_hierarchy": self.reporting_hierarchy},
            fields=["name", "member_code", "parent_member",
                    "effective_from", "effective_to"],
        )
        code_of = {r.name: r.member_code for r in rows}
        windows = [_window(r.effective_from, r.effective_to)
                   for r in rows if r.member_code == code and r.name != self.name]
        if replacement:
            windows.append(replacement)
        for child in rows:
            if child.name == self.name:
                continue
            linked_here = child.parent_member == self.name
            if moving:
                if not linked_here:
                    continue
            elif code_of.get(child.parent_member) != code or (
                moving is False and linked_here
            ):
                continue
            gaps = _uncovered(
                *_window(child.effective_from, child.effective_to), windows
            )
            if gaps:
                frappe.throw(
                    f"{child.member_code} ({child.name}) would lose its parent "
                    f"{code} for {', '.join(_span(a, b) for a, b in gaps)}. "
                    "Change or end that row first."
                )

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
        """Walk up by CODE, not by row link: only a parent's code matters, so
        tranches can loop where the row links don't (B1 root; A2 under B1;
        B2 under A2 makes A and B each other's parent from B2's start). From
        the parent code with this row's window, follow every tranche whose
        window meets the running one to its own parent code, narrowing the
        window; getting back to this row's code with days left is a cycle
        during those days."""
        if not self.parent_member:
            return
        me = self.member_code
        tranches = {}
        for r in frappe.get_all(
            "Reporting Hierarchy Member",
            filters={"reporting_hierarchy": self.reporting_hierarchy},
            fields=["name", "member_code", "parent_member",
                    "effective_from", "effective_to"],
        ):
            tranches[r.name] = (r.member_code, r.parent_member,
                                *_window(r.effective_from, r.effective_to))
        tranches[self.name] = (me, self.parent_member, *self._window())

        def code_of(link):
            if not link:
                return None
            if link in tranches:
                return tranches[link][0]
            return frappe.db.get_value(
                "Reporting Hierarchy Member", link, "member_code"
            )

        first = code_of(self.parent_member)
        if first == me:
            frappe.throw(f"{me} cannot be its own parent.")
        by_code = {}
        for code, link, a, b in tranches.values():
            by_code.setdefault(code, []).append((link, a, b))
        start, end = self._window()
        stack = [(first, start, end, [me, first])]
        while stack:
            code, a, b, chain = stack.pop()
            if len(chain) > _MAX_DEPTH:
                continue
            for link, t_start, t_end in by_code.get(code, []):
                lo, hi = max(a, t_start), min(b, t_end)
                if lo > hi:
                    continue
                up = code_of(link)
                if not up:
                    continue
                if up == me:
                    frappe.throw(
                        f"Parent chain forms a cycle ({' → '.join(chain + [me])}) "
                        f"during {_span(lo, hi)}."
                    )
                stack.append((up, lo, hi, chain + [up]))
"""Ownership Period — the ONE place ownership lives.

PRD-9: effective date ranges for ownership changes
PRD-11: step acquisitions (acquisition_price, fair_value_adjustment, goodwill)
PRD-12: disposals (disposal_date, disposal_price)

F2: Consolidation Group no longer carries ownership_pct or consolidation_method.
A group's share of a subsidiary changes on a date, and a doctype field cannot say
when — so ownership was in two grains with no rule about which won, and the dbt
side invented one that read a genuine 0% as "unset".

A period describes **this node**: how much of it its parent owns, from
``effective_date`` until ``end_date``. The node is ``(consolidation_group,
data_area_id)``, the same pair that names a Consolidation Group document, so a
sub-group is ownable too — leave ``data_area_id`` blank for a group node. Root
nodes are 100% by construction and take no period: nobody owns the top.
"""
import datetime

import frappe
from frappe.model.document import Document
from frappe.utils import getdate

from konsol.clickhouse import sync_doctype_after_commit
from konsol.period_status import assert_open_between, first_period_affected

# ClickHouse's Date type holds 1970-01-01 .. 2149-06-06 and CLAMPS anything
# outside it without complaining, so a period dated 1900 or 9999 would say one
# thing in Frappe and another in the warehouse. A blank end_date is the normal
# "still open" case and is written as the column's own default; an explicit date
# has to be representable.
_CH_DATE_MIN = "1970-01-01"
_CH_DATE_MAX = "2149-06-06"
_OPEN_ENDED = _CH_DATE_MAX

# data_area_id is a Link, and a blank Link is stored as NULL — so {"data_area_id":
# ""} matches nothing and every lookup below would silently miss every group
# node. ["is", "not set"] renders as `IS NULL OR = ''` and matches both
# spellings (the same trap that made Dimension Mapping's uniqueness guard inert,
# konsol #112).
_BLANK = ["is", "not set"]

#: The deal figures (konsolidat#198). They are filled by the Business
#: Combination that opens a period or the Business Disposal that closes it,
#: under ``frappe.flags.from_business_combination``; typed here they would be a
#: second, unreviewed source for the same numbers.
DEAL_DATE_FIELDS = ("acquisition_date", "disposal_date")
DEAL_AMOUNT_FIELDS = ("is_first_acquisition", "acquisition_price", "fair_value_adjustment",
                      "is_disposal", "disposal_price")
DEAL_FIELDS = DEAL_DATE_FIELDS + DEAL_AMOUNT_FIELDS


def _deal_value(field, value):
    """Compared normalised: the saved document comes from the database (ints,
    floats, date objects, NULL as None) while the form posts strings and "",
    and "0" versus 0, or "" versus None, is not a change."""
    if value in (None, ""):
        return None if field in DEAL_DATE_FIELDS else 0.0
    if field in DEAL_DATE_FIELDS:
        return getdate(value)
    return float(value)


def _date_or_none(value):
    """A blank end date is "open-ended", never today (``getdate(None)`` is today)."""
    return getdate(value) if value not in (None, "") else None


class OwnershipPeriod(Document):
    CH_TABLE = "epm_staging.ownership_periods"
    CH_FIELD_MAP = {
        "consolidation_group": "consolidation_group",
        "data_area_id": "data_area_id",
        "effective_date": "effective_date",
        "end_date": "end_date",
        "ownership_pct": "ownership_pct",
        "consolidation_method": "consolidation_method",
        "acquisition_date": "acquisition_date",
        "is_first_acquisition": "is_first_acquisition",
        "acquisition_price": "acquisition_price",
        "fair_value_adjustment": "fair_value_adjustment",
        "disposal_date": "disposal_date",
        "disposal_price": "disposal_price",
        "is_disposal": "is_disposal",
    }

    def validate(self):
        self._validate_deal_fields_untouched()
        self._validate_node_exists()
        self._validate_pct_range()
        self._validate_dates_representable()
        self._check_no_gaps_or_overlaps()

    def before_cancel(self):
        """Cancel only while every period this ownership covers is open (#136):
        the span is ``cancel_span()``, one rule shared with the deal documents
        that undo a period in their own name."""
        start, end, exclusive = self.cancel_span()
        assert_open_between(start, end, action="cancel an ownership period", end_exclusive=exclusive)

    def cancel_span(self):
        """The declared periods a cancel of this period changes, as
        ``(start, end_or_None, end_exclusive)`` for ``assert_open_between``:
        from the first period its effective date affects until its end date
        or the next submitted period for the same group and entity, whichever
        comes first (the warehouse takes the latest effective_date <= the
        period, so from the first declared period on or after the next
        period's start this one is no longer read — that bound is exclusive),
        and open-ended with neither.

        One rule, computed here: a Business Combination cancelling or
        clearing the period it started gates the same span with its own
        sentence (PR #202 third review, finding 2). Two spans disagreed
        before — the deal's ran to "today" for an open-ended period, so a
        closed period after today passed its gate and was refused by this
        one, in the wrong document's words.
        """
        next_start = frappe.db.get_value(
            "Ownership Period",
            {"consolidation_group": self.consolidation_group, "data_area_id": self.data_area_id,
             "docstatus": 1, "effective_date": [">", self.effective_date], "name": ["!=", self.name]},
            "effective_date", order_by="effective_date asc")
        end, exclusive = getdate(self.end_date) if self.end_date else None, False
        if next_start:
            next_first = first_period_affected(next_start)
            if next_first is not None and (end is None or end >= next_first):
                end, exclusive = next_first, True
        return getdate(self.effective_date), end, exclusive

    def on_submit(self):
        self._end_predecessor()
        sync_doctype_after_commit(self.doctype, self.CH_TABLE, self.CH_FIELD_MAP)

    def on_cancel(self):
        self._restore_predecessor()
        sync_doctype_after_commit(self.doctype, self.CH_TABLE, self.CH_FIELD_MAP)

    # -- ownership change (#305-Q1-1, Deepak Pai 7 Oct 2026) -------------------
    #
    # Approving a change end-dates the period it supersedes to the change's
    # effective_date - 1 day; a cancel restores the end date the draft stored
    # (``superseded_end_date``, blank = open-ended). Rejected: Q1-2 (cancel and
    # amend the current period) and Q1-3 (changes only through a Business
    # Combination / Disposal). The predecessor is written with ``db_set`` under
    # ``frappe.flags.from_ownership_change`` (the Business Disposal pattern) and
    # re-synced after the commit. The flag does NOT open the deal-field guard.

    def _end_predecessor(self):
        name = self.get("supersedes")
        if not name:
            return
        predecessor = frappe.get_doc("Ownership Period", name, for_update=True)
        stored, now = _date_or_none(self.get("superseded_end_date")), _date_or_none(predecessor.get("end_date"))
        if stored != now:
            frappe.throw(
                f"{name} changed since this change was drafted: its end date is now "
                f"{now or 'open'}, the draft recorded {stored or 'open'}. Delete this draft "
                f"and record the change again from the current ownership."
            )
        self._write_predecessor_end(predecessor, getdate(self.effective_date) - datetime.timedelta(days=1))

    def _restore_predecessor(self):
        name = self.get("supersedes")
        if not name or not frappe.db.exists("Ownership Period", name):
            # nothing superseded, or the predecessor was deleted since: no end to give back
            return
        predecessor = frappe.get_doc("Ownership Period", name, for_update=True)
        restored = _date_or_none(self.get("superseded_end_date"))
        start, end = getdate(predecessor.effective_date), restored or getdate(_OPEN_ENDED)
        later = [
            op for op in frappe.get_all(
                self.doctype,
                filters={"consolidation_group": self.consolidation_group,
                         "data_area_id": self.data_area_id or _BLANK, "docstatus": 1,
                         "name": ["!=", self.name]},
                fields=["name", "effective_date"], limit_page_length=0)
            if op.name != name and start < getdate(op.effective_date) <= end
        ]
        if later:
            frappe.throw(
                f"Cancelling this change gives {name} back its end date ({restored or 'open'}), "
                f"which would overlap " + ", ".join(sorted(op.name for op in later))
                + ". Cancel the later period(s) first."
            )
        self._write_predecessor_end(predecessor, restored)

    def _write_predecessor_end(self, predecessor, end_date):
        frappe.flags.from_ownership_change = True
        try:
            predecessor.db_set("end_date", end_date)
        finally:
            frappe.flags.from_ownership_change = False
        sync_doctype_after_commit(self.doctype, self.CH_TABLE, self.CH_FIELD_MAP)

    def after_delete(self):
        """after_delete, not on_trash: on_trash runs before the row is gone, so
        the full-table re-send put it straight back (#120)."""
        sync_doctype_after_commit(self.doctype, self.CH_TABLE, self.CH_FIELD_MAP)

    # -- validation ---------------------------------------------------------

    def _validate_deal_fields_untouched(self):
        """The deal fields belong to the deal documents (konsolidat#198 P8).

        A Business Combination computes the price and the fair value
        adjustments from declared consideration and acquired balances, under
        approval; a Business Disposal does the same for the disposal. Those
        documents write the figures onto this period under
        ``frappe.flags.from_business_combination`` (create, update-and-submit,
        or ``db_set`` on a submitted period — and the reset to blanks when a
        disposal is cancelled). A patch (``frappe.flags.in_patch``) carries old
        rows across. Any other change to them is refused: a new period may not
        carry deal figures, and a saved one may not have them edited.
        """
        if frappe.flags.from_business_combination or frappe.flags.in_patch:
            return
        before = self.get_doc_before_save()
        changed = [
            field for field in DEAL_FIELDS
            if _deal_value(field, self.get(field))
            != _deal_value(field, before.get(field) if before is not None else None)
        ]
        if changed:
            frappe.throw(
                "Record the acquisition as a Business Combination (or the disposal as a Business Disposal); these fields are filled from it."
            )

    def _node(self):
        """The Consolidation Group node this period describes, or None."""
        return frappe.db.get_value(
            "Consolidation Group",
            {"consolidation_group": self.consolidation_group,
             "data_area_id": self.data_area_id or _BLANK},
            ["name", "parent_consolidation_group"],
            as_dict=True,
        )

    def _validate_node_exists(self):
        """A period must describe a real node, and not a root.

        Ownership is now the only grain, so a period naming a node that does not
        exist is silently ignored by every consumer — the failure mode this
        doctype exists to remove. A root node has no parent to own it; its
        chain share is 100% by construction and a period on it would never be
        read by anything (see ConsolidationGroup.build_rows: a chain holds only
        the nodes strictly BELOW the ancestor).
        """
        node = self._node()
        if not node:
            where = f"entity '{self.data_area_id}'" if self.data_area_id else "the group node"
            frappe.throw(
                f"No Consolidation Group node '{self.consolidation_group}' / "
                f"{where}. Create the node first — an Ownership Period for a "
                f"node that does not exist is read by nothing."
            )
        if not node.parent_consolidation_group:
            frappe.throw(
                f"'{self.consolidation_group}' is a root group: nobody owns the "
                f"top of a hierarchy, so it is 100% by construction and an "
                f"Ownership Period on it would never be applied."
            )

    def _validate_pct_range(self):
        if self.ownership_pct is None or not (0 <= float(self.ownership_pct) <= 100):
            frappe.throw(
                f"ownership_pct must be between 0 and 100 (got {self.ownership_pct}). "
                f"0 is allowed and means 0 — it is not a way to say 'unknown'."
            )

    #: the old doctype default for "open ended", which ClickHouse cannot hold
    _LEGACY_OPEN = "9999-12-31"

    def _validate_dates_representable(self):
        """Refuse a date ClickHouse would silently clamp.

        Every date on this doctype is synced into a ClickHouse Date column, whose
        range is 1970-01-01 .. 2149-06-06. Outside it the value is clamped with
        no error, so the document and the warehouse quietly disagree — and an
        ownership period is exactly where that matters.
        """
        # end_date used to default to 9999-12-31 as a way of saying "open".
        # Blank says the same thing and is representable, so translate the old
        # sentinel rather than rejecting every row written before F2. Any OTHER
        # out-of-range date is a mistake and is refused.
        if self.end_date and str(self.end_date).startswith(self._LEGACY_OPEN):
            self.end_date = None

        lo, hi = getdate(_CH_DATE_MIN), getdate(_CH_DATE_MAX)
        for field in ("effective_date", "end_date", "acquisition_date", "disposal_date"):
            value = self.get(field)
            if not value:
                continue
            if not (lo <= getdate(value) <= hi):
                frappe.throw(
                    f"{field} {value} is outside the range the warehouse can "
                    f"store ({_CH_DATE_MIN} to {_CH_DATE_MAX}); it would be "
                    f"silently clamped. Use a date inside that range — "
                    f"{_CH_DATE_MIN} means 'as long as there has been data' and "
                    f"a blank end_date means 'still open'."
                )

    def _check_no_gaps_or_overlaps(self):
        """PRD-9: no two live periods may cover the same date for one node.

        Load-bearing since F2: two overlapping periods would both match the
        ASOF lookup and fan every fact row out. Dates are normalised with
        getdate() on both sides — frappe.get_all returns date objects while the
        document's own field is a string, and comparing the two raises.
        """
        if not self.effective_date:
            return
        start = getdate(self.effective_date)
        end = getdate(self.end_date or _OPEN_ENDED)
        if end < start:
            frappe.throw(
                f"end_date {self.end_date} is before effective_date {self.effective_date}."
            )
        others = frappe.get_all(
            self.doctype,
            filters={
                "consolidation_group": self.consolidation_group,
                "data_area_id": self.data_area_id or _BLANK,
                "name": ["!=", self.name],
                "docstatus": ["!=", 2],
            },
            fields=["name", "effective_date", "end_date", "docstatus"],
            limit_page_length=0,
        )
        supersedes = self.get("supersedes")
        # #305-Q1-1: the period this change supersedes is exempt ONLY when it is
        # submitted, belongs to this node (``others`` holds only this node's
        # periods), starts before this change and covers its first day — the
        # period approval ends at effective_date - 1. Anything else it names
        # is checked like any other period.
        predecessor = next(
            (op for op in others
             if supersedes and op.name == supersedes and int(op.docstatus or 0) == 1
             and getdate(op.effective_date) < start <= getdate(op.end_date or _OPEN_ENDED)),
            None)
        for op in others:
            if predecessor is not None and op.name == predecessor.name:
                continue
            if start <= getdate(op.end_date or _OPEN_ENDED) and end >= getdate(op.effective_date):
                frappe.throw(
                    f"Ownership period overlaps with {op.name} "
                    f"({op.effective_date} to {op.end_date or 'open'})"
                )
        if not supersedes:
            return
        if predecessor is None:
            frappe.throw(
                f"{supersedes} cannot be superseded by this change: a change supersedes the "
                f"approved Ownership Period of the same node that starts before "
                f"{self.effective_date} and covers that day."
            )
        later = sorted((op for op in others if getdate(op.effective_date) > start),
                       key=lambda op: getdate(op.effective_date))
        if later:
            frappe.throw(
                f"A change cannot go between two existing periods: {later[0].name} starts "
                f"{later[0].effective_date}, after {self.effective_date}. Change the latest period instead."
            )

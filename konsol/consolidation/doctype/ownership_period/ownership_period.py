"""Ownership Period — the ONE place ownership lives.

PRD-9: effective date ranges for ownership changes
PRD-11: step acquisitions (acquisition_price, fair_value_adjustment, goodwill)
PRD-12: disposals (disposal_date, disposal_price)

F2: Consolidation Group no longer carries ownership_pct or consolidation_method.
A group's share of a subsidiary changes on a date, and a doctype field cannot say
when — so ownership was in two grains with no rule about which won, and the dbt
side invented one that read a genuine 0% as "unset".

A period is a **link** of the ownership graph (konsol#117, option A, Deepak Pai
7 Nov 2026): how much of the **owned node** ``(consolidation_group,
data_area_id)`` the **owner node** ``owner_node`` holds, from ``effective_date``
until ``end_date``. The owned node is the same pair that names a Consolidation
Group document, so a sub-group is ownable too — leave ``data_area_id`` blank for
a group node. The owner is any node of the same hierarchy: an entity node or a
pure group node ("the group as the notional holder"). Root nodes are 100% by
construction and take no period: nobody owns the top.

The owner is pre-filled with the owned node's tree parent when left blank. That
is the one default in the design: it is visible on the form, named in the
field's description, and refused when the parent is not a node. A node may
have several owners; the links into it over any one date sum to at most 100%.
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


def _pct(value):
    """A percentage as typed: 80 for 80.0, 10.5 for 10.5."""
    return f"{float(value or 0):g}"


def _date_or_none(value):
    """A blank end date is "open-ended", never today (``getdate(None)`` is today)."""
    return getdate(value) if value not in (None, "") else None


class OwnershipPeriod(Document):
    CH_TABLE = "epm_staging.ownership_periods"
    CH_FIELD_MAP = {
        "consolidation_group": "consolidation_group",
        "data_area_id": "data_area_id",
        # #117 pair 1: the owner node's group and entity code, resolved from
        # owner_node on every save (``_resolve_owner``). The columns are task
        # C's DDL (fixed names, ``tasks-117-pair1.md``); until it lands a sync
        # fails on the missing columns and the live table is left as it was.
        "owner_group": "owner_group",
        "owner_data_area_id": "owner_data_area_id",
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

    def before_insert(self):
        """Frappe v16 names a document after ``before_insert`` and before
        ``validate``, so the owner (and ``owner_label``, which the autoname
        reads) is resolved here first."""
        self._resolve_owner()

    def before_validate(self):
        """On every save: a cleared owner is pre-filled again, and the derived
        fields follow an edited owner."""
        self._resolve_owner()

    def validate(self):
        self._validate_deal_fields_untouched()
        self._validate_node_exists()
        self._validate_owner()
        self._validate_pct_range()
        self._validate_consolidation_method()
        self._validate_dates_representable()
        self._check_no_gaps_or_overlaps()
        self._check_total_issued()

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
        or the next submitted period on the same (owner, owned) edge,
        whichever comes first (the warehouse takes the latest effective_date
        <= the period per link, so from the first declared period on or after
        the next period's start this one is no longer read — that bound is
        exclusive), and open-ended with neither.

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
             "owner_node": self.owner_node,
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
    # Combination / Disposal). The predecessor's end date is written with
    # ``db_set`` and re-synced after the commit; the deal-field guard is not
    # involved (end_date is not a deal field).

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
        if int(predecessor.get("docstatus") or 0) == 2:
            # cancelled since (review S16): it is the audit trail, never written again
            return
        restored = _date_or_none(self.get("superseded_end_date"))
        start, end = getdate(predecessor.effective_date), restored or getdate(_OPEN_ENDED)
        later = [
            op for op in frappe.get_all(
                self.doctype,
                filters={"consolidation_group": self.consolidation_group,
                         "data_area_id": self.data_area_id or _BLANK,
                         "owner_node": self.owner_node, "docstatus": 1,
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
        predecessor.db_set("end_date", end_date)
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

    # -- the owner (#117 pair 1) ---------------------------------------------

    def _owner(self):
        """The Consolidation Group node ``owner_node`` names, or None."""
        if not self.owner_node:
            return None
        return frappe.db.get_value(
            "Consolidation Group", self.owner_node,
            ["name", "consolidation_group", "data_area_id", "parent_consolidation_group"],
            as_dict=True,
        )

    def _resolve_owner(self):
        """Pre-fill a blank owner with the owned node's tree parent (the one
        default, named on the field), then derive ``owner_group``,
        ``owner_data_area_id`` and ``owner_label`` from the owner node.

        Runs in ``before_insert`` (v16 names the document right after it, and
        the autoname reads ``owner_label``) and in ``before_validate`` (so an
        edited owner never leaves a stale label or warehouse column). An owner
        that names no node leaves the three blank; ``_validate_owner`` refuses
        it by name.
        """
        if not self.owner_node:
            node = self._node()
            if node and node.parent_consolidation_group:
                self.owner_node = node.parent_consolidation_group
        owner = self._owner()
        self.owner_group = owner.consolidation_group if owner else None
        self.owner_data_area_id = (owner.data_area_id or None) if owner else None
        self.owner_label = (owner.data_area_id or owner.consolidation_group) if owner else None

    def _owned_label(self):
        """How the owned node is named in a sentence: its entity code, else
        its group code (as ``owner_label`` does for the owner)."""
        return self.data_area_id or self.consolidation_group

    def _root_of(self, node):
        """The root of the hierarchy ``node`` sits in, by walking tree parents
        (a NestedSet has no cycles; the live tree is six deep at most)."""
        seen = set()
        while node.parent_consolidation_group and node.name not in seen:
            seen.add(node.name)
            parent = frappe.db.get_value(
                "Consolidation Group", node.parent_consolidation_group,
                ["name", "consolidation_group", "data_area_id", "parent_consolidation_group"],
                as_dict=True)
            if parent is None:
                break
            node = parent
        return node.name

    def _validate_owner(self):
        """§1 refusal 1: the owner is a node, is not the owned node, and sits
        under the same root. A blank owner is the mandatory check's to refuse
        (after ``_resolve_owner`` found no parent to pre-fill it with)."""
        if not self.owner_node:
            return
        owner = self._owner()
        if owner is None:
            frappe.throw(f"Owner Node {self.owner_node} is not a Consolidation Group node.")
        node = self._node()
        if owner.name == node.name:
            frappe.throw(
                f"`{self._owned_label()}` cannot own itself: Owner Node {owner.name} is the "
                f"owned node. Name the node that holds the share."
            )
        owner_root, owned_root = self._root_of(owner), self._root_of(node)
        if owner_root != owned_root:
            frappe.throw(
                f"Owner Node {owner.name} is under root {owner_root}, not under {owned_root} "
                f"like `{self._owned_label()}`: an owner is a node of the same hierarchy as "
                f"the node it owns."
            )

    def _validate_pct_range(self):
        if self.ownership_pct is None or not (0 <= float(self.ownership_pct) <= 100):
            frappe.throw(
                f"ownership_pct must be between 0 and 100 (got {self.ownership_pct}). "
                f"0 is allowed and means 0 — it is not a way to say 'unknown'."
            )

    def _validate_consolidation_method(self):
        """Refuse a method the warehouse does not compute (konsol#429).

        gold_consolidated_trial_balance excludes only ``equity`` and ``none``
        and computes group_amount = translated x pct and nci_amount =
        translated x (1 - pct) with no method branch, so a period declared
        ``proportional`` consolidates as a full subsidiary with NCI. Proportional
        consolidation is the venturer's share of each line with no NCI: the
        number would be wrong and nothing would say so. The Select keeps the
        option (#117 decides whether to implement it for IFRS 11 joint
        operations or drop it); this guard is what makes it safe to offer.
        """
        if self.consolidation_method == "proportional":
            frappe.throw(
                "Proportional consolidation is not implemented (konsol#429): the "
                "warehouse would consolidate this period as full with NCI, which is "
                "not proportional consolidation. Use full with NCI, or equity."
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
        """PRD-9: no two live periods may cover the same date for one link.

        The link is the (owner, owned) edge (#117 pair 1): two owners may hold
        one node over the same dates, those are two links; one owner twice is
        an overlap. Load-bearing since F2: two overlapping periods of one link
        would both match the ASOF lookup and fan every fact row out. Dates are
        normalised with getdate() on both sides — frappe.get_all returns date
        objects while the document's own field is a string, and comparing the
        two raises.
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
                "owner_node": self.owner_node,
                "name": ["!=", self.name],
                "docstatus": ["!=", 2],
            },
            fields=["name", "effective_date", "end_date", "docstatus"],
            limit_page_length=0,
            order_by="modified desc",
        )
        supersedes = self.get("supersedes")
        # #305-Q1-1: the period this change supersedes is exempt ONLY when it is
        # submitted, belongs to this link (``others`` holds only this edge's
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
        # Review S6: a change takes over the rest of its predecessor's span, so it
        # ends where the predecessor ended (``superseded_end_date``, blank = open).
        # Any other end date would make the change's effect say one span and the
        # warehouse hold another.
        stored = _date_or_none(self.get("superseded_end_date"))
        if _date_or_none(self.end_date) != stored:
            frappe.throw(
                f"{self.name} supersedes {supersedes}, so it ends where {supersedes} ended "
                f"({stored or 'open'}): clear or correct End Date."
            )
        later = sorted((op for op in others if getdate(op.effective_date) > start),
                       key=lambda op: getdate(op.effective_date))
        if later:
            frappe.throw(
                f"A change cannot go between two existing periods: {later[0].name} starts "
                f"{later[0].effective_date}, after {self.effective_date}. Change the latest period instead."
            )

    def _check_total_issued(self):
        """§1 refusal 4 (#117): the links into one node whose windows overlap
        this one must not sum past 100%.

        Checked at every date this period covers where the set of other
        holders changes (its own start and each other link's start inside it):
        the holders covering that date are summed, and the largest total is the
        one named, with the window where those holders all hold. A cancelled
        link holds nothing. The period this change supersedes is left out: the
        approval ends it the day before this one starts, so its share is the
        share being changed, not a second one. Runs after
        ``_check_no_gaps_or_overlaps``, which has already vouched for that
        predecessor.
        """
        if not self.effective_date:
            return
        start = getdate(self.effective_date)
        end = getdate(self.end_date or _OPEN_ENDED)
        others = [
            op for op in frappe.get_all(
                self.doctype,
                filters={
                    "consolidation_group": self.consolidation_group,
                    "data_area_id": self.data_area_id or _BLANK,
                    "name": ["!=", self.name],
                    "docstatus": ["!=", 2],
                },
                fields=["name", "owner_label", "ownership_pct", "effective_date", "end_date"],
                limit_page_length=0,
                order_by="effective_date asc",
            )
            if op.name != self.get("supersedes")
            and start <= getdate(op.end_date or _OPEN_ENDED) and end >= getdate(op.effective_date)
        ]
        if not others:
            return
        points = sorted({start} | {getdate(op.effective_date) for op in others
                                   if start < getdate(op.effective_date) <= end})
        worst, holders = None, []
        for point in points:
            covering = [op for op in others
                        if getdate(op.effective_date) <= point <= getdate(op.end_date or _OPEN_ENDED)]
            total = sum(float(op.ownership_pct or 0) for op in covering)
            if worst is None or total > worst[1]:
                worst, holders = (point, total), covering
        point, total = worst
        if round(total + float(self.ownership_pct or 0), 6) <= 100:
            return
        # the window where every named holder holds: from the point to the
        # earliest end among them, open when none ends
        ends = [getdate(op.end_date) for op in holders if op.end_date]
        until = min(ends).isoformat() if ends else "open"
        listed = ", ".join(
            f"`{op.owner_label}` {_pct(op.ownership_pct)}%"
            for op in sorted(holders, key=lambda op: (-float(op.ownership_pct or 0), op.owner_label or "")))
        frappe.throw(
            f"`{self._owned_label()}` is already {_pct(total)}% owned in {point.isoformat()}..{until} "
            f"({listed}); this {_pct(self.ownership_pct)}% would exceed 100%."
        )

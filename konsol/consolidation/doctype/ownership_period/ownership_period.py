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
import frappe
from frappe.model.document import Document
from frappe.utils import getdate

from konsol.clickhouse import sync_doctype_after_commit
from konsol.period_status import assert_open_between

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
        self._validate_node_exists()
        self._validate_pct_range()
        self._validate_dates_representable()
        self._check_no_gaps_or_overlaps()

    def before_cancel(self):
        """Cancel only while every period this ownership covers is open (#136):
        from the first period its effective date affects to its end date, or
        indefinitely with no end."""
        assert_open_between(self.effective_date, self.end_date, action="cancel an ownership period")

    def on_submit(self):
        sync_doctype_after_commit(self.doctype, self.CH_TABLE, self.CH_FIELD_MAP)

    def on_cancel(self):
        sync_doctype_after_commit(self.doctype, self.CH_TABLE, self.CH_FIELD_MAP)

    def after_delete(self):
        """after_delete, not on_trash: on_trash runs before the row is gone, so
        the full-table re-send put it straight back (#120)."""
        sync_doctype_after_commit(self.doctype, self.CH_TABLE, self.CH_FIELD_MAP)

    # -- validation ---------------------------------------------------------

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
            fields=["name", "effective_date", "end_date"],
            limit_page_length=0,
        )
        for op in others:
            if start <= getdate(op.end_date or _OPEN_ENDED) and end >= getdate(op.effective_date):
                frappe.throw(
                    f"Ownership period overlaps with {op.name} "
                    f"({op.effective_date} to {op.end_date or 'open'})"
                )

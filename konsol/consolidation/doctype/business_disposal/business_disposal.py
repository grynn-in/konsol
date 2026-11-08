"""Business Disposal: one document per loss of control (konsolidat#198).

Three layers, kept apart on purpose (design 2a):

* the **disposal is a declared input** — this document's lines: what was
  received for the holding, and on which date control was lost;
* the **policy is configured**, once, on the Consolidation Group node named
  by ``consolidation_group`` (the group root carries the Consolidation Policy
  and the declared accounts the disposal journal posts to, design 1/1a);
* the **mechanics are programmed** in the pure, host-tested
  ``konsol.business_disposal_model``: this controller looks the facts up (the
  declared period the disposal date falls in, the period's Closing group
  rates, the chart, the entity's current Ownership Period) and feeds them in.
  The gain or loss itself is measured downstream by the consolidation journal
  (design 5) from the entity's balances at the disposal date.

The disposal names the **disposer** (#117 option A, Deepak Pai 7 Nov 2026):
a node may have several owners, so ``disposer_node``, the Consolidation
Group node whose holding is sold, says which link the disposal measures
against and closes. It is pre-filled from the disposed node's tree parent
when blank (the one default, visible on the form and in the field's
description) and refused by the Ownership Period's own owner sentences
(``owner_refusal``), in this document's name.

Lifecycle: ``before_validate`` pre-fills the disposer; ``validate`` computes
the Result and throws the model's sentences, and holds to one holding, one
disposal: a period an approved disposal already
closed is not sold again until that disposal is cancelled or amended;
``before_submit`` re-checks the period is open and the accounts are declared;
``on_submit`` closes the entity's Ownership Period on the disposal date and
writes the approved disposal to the warehouse. Submit = approval (the
workflow); cancel only while the disposal period is open and no later
Ownership Period of the entity has been approved since, and it gives the
Ownership Period the approval closed back what it held before. Nothing here
saves the document from a hook or commits.
"""
import datetime

import frappe
from frappe.model.document import Document
from frappe.utils import getdate

from konsol.business_disposal_model import problems, totals
from konsol.clickhouse import sync_doctype_after_commit
from konsol.joint_operation_model import joint_operation_problem
from konsol.close.reject_reason import refuse_reasonless_reject
from konsol.consolidation.doctype.business_disposal_proceeds.business_disposal_proceeds import (
    BusinessDisposalProceeds,
)
from konsol.consolidation.doctype.ownership_period.ownership_period import node_by_key, owner_refusal
from konsol.consolidation_policy_model import (
    ACCOUNT_FIELDS,
    POLICY_FIELDS,
    account_problems,
    required_accounts,
)
from konsol.group_rates import true_rate
from konsol.period_status import PeriodNotDeclared, assert_open

#: A blank Link is stored as NULL: match both spellings (see Ownership Period).
_BLANK = ["is", "not set"]

_PREFIX = "Business Disposal: "

#: The disposal as ``required_accounts`` reads it: goodwill, gain/loss, proceeds.
_DEAL = {"kind": "disposal"}

#: Reopen Data field on the disposal -> the Ownership Period field it mirrors.
#: The approval records the period's values here before closing it; the
#: cancel writes exactly these back (PR #202 finding 4).
_REOPEN_FIELDS = {
    "previous_end_date": "end_date",
    "previous_is_disposal": "is_disposal",
    "previous_disposal_date": "disposal_date",
    "previous_disposal_price": "disposal_price",
}


def _period_value(field, value):
    """An Ownership Period deal value as ``db_set`` should write it: a blank
    date is None (not ""), the flag an int, the price a float."""
    if field == "is_disposal":
        return int(value or 0)
    if field == "disposal_price":
        return float(value or 0)
    return value or None


class BusinessDisposal(Document):
    CH_TABLE = "epm_staging.business_disposals"
    CH_FIELD_MAP = {
        "name": "name",
        "consolidation_group": "consolidation_group",
        "disposed_entity": "disposed_entity",
        "disposal_date": "disposal_date",
        "share_disposed_pct": "share_disposed_pct",
        "retained_interest_pct": "retained_interest_pct",
        "proceeds_currency": "proceeds_currency",
        "total_proceeds": "total_proceeds",
        "ownership_period": "ownership_period",
    }
    #: child doctype -> its controller (CH_TABLE / CH_FIELD_MAP): the child
    #: table is its own doctype in the warehouse, keyed (parent, idx).
    CHILD_CONTROLLERS = {
        "Business Disposal Proceeds": BusinessDisposalProceeds,
    }

    # -- lifecycle -----------------------------------------------------------

    def before_validate(self):
        self._resolve_disposer()

    def validate(self):
        # konsol#310: a Reject without approval_api.reject's reason is
        # refused before anything else is computed.
        refuse_reasonless_reject(self)
        period = self._disposal_period()
        root = self._root()
        self._validate_disposer()
        self._joint_operation_problem(root.get("name"))

        rate_to_group = self._rate_to_group(root.get("reporting_currency"), period)
        try:
            result = totals(self, self._lines(), rate_to_group)
        except ValueError as e:  # a proceeds currency with no Closing rate
            frappe.throw(
                f"{_PREFIX}{e} ({period['period_code']} of FY{period['fiscal_year']}): "
                f"approve a Closing Group Exchange Rate to {root.get('reporting_currency')} for it."
            )
        self.total_proceeds = float(result["total_proceeds"])

        holding = self._holding()
        facts = {
            "current_holding_pct": holding["ownership_pct"] if holding else None,
            "totals": result,
            "is_published_leaf": self._is_published_leaf,
        }
        found = problems(self, self._lines(), root, facts)
        found += self._closed_holding_problems(holding)
        if found:
            frappe.throw("<br>".join(found))

    def before_submit(self):
        """Approval: the disposal period is still open, and the accounts the
        disposal journal posts to are still declared on the root (it may have
        changed since the disposal was saved)."""
        period = self._disposal_period()
        assert_open(period["fiscal_year"], period["fiscal_period"],
                    action="approve a business disposal")
        root = self._root()
        found = account_problems(root, required_accounts(root, _DEAL), self._is_published_leaf)
        if found:
            frappe.throw("<br>".join(found))

    def on_submit(self):
        self._close_ownership_period()
        self._sync()

    def before_cancel(self):
        period = self._disposal_period()
        assert_open(period["fiscal_year"], period["fiscal_period"],
                    action="cancel a business disposal")
        self._assert_no_later_ownership_period()

    def on_cancel(self):
        self._reopen_ownership_period()
        self._sync()

    def after_delete(self):
        """after_delete, not on_trash: on_trash runs before the row is gone,
        so the full-table re-send put it straight back (#120)."""
        self._sync()

    # -- facts the model needs ---------------------------------------------

    def _lines(self):
        return list(self.get("proceeds") or [])

    def _disposal_period(self):
        """The declared fiscal period the disposal date falls in (konsol#189:
        periods are declared rows of an EPM Fiscal Year, never ``date.month``).
        A Regular period wins over an Opening/Closing/Adjustment period that
        shares the date. No period → refused."""
        rows = frappe.db.sql(
            "SELECT y.fiscal_year, p.fiscal_period, p.period_code, p.period_type "
            "FROM `tabEPM Fiscal Year Period` p "
            "JOIN `tabEPM Fiscal Year` y ON y.name = p.parent "
            "WHERE p.parentfield = 'periods' AND p.start_date <= %(d)s AND p.end_date >= %(d)s "
            "ORDER BY (p.period_type <> 'Regular'), p.start_date LIMIT 1",
            {"d": getdate(self.disposal_date)},
            as_dict=True,
        )
        if not rows:
            frappe.throw(
                f"{_PREFIX}no declared fiscal period covers {self.disposal_date}: "
                "declare it in EPM Fiscal Year before recording the disposal.",
                PeriodNotDeclared,
            )
        row = rows[0]
        return {
            "fiscal_year": int(row["fiscal_year"]),
            "fiscal_period": int(row["fiscal_period"]),
            "period_code": row["period_code"],
        }

    def _root(self):
        """The group node of ``consolidation_group`` (no entity): it carries
        the Consolidation Policy and the declared accounts (design 1/1a) and
        the reporting currency the proceeds are measured in."""
        fields = ["name", "reporting_currency", *POLICY_FIELDS, *ACCOUNT_FIELDS]
        root = frappe.db.get_value(
            "Consolidation Group",
            {"consolidation_group": self.consolidation_group, "data_area_id": _BLANK},
            fields,
            as_dict=True,
        )
        if not root:
            frappe.throw(
                f"{_PREFIX}no Consolidation Group node '{self.consolidation_group}' without an "
                "entity: the group root carries the Consolidation Policy the disposal is measured under."
            )
        return root

    # -- the disposer (#117 pair 1) -------------------------------------------

    def _disposed_node(self):
        """The Consolidation Group node of the disposed entity in this group,
        or None when the tree does not have it."""
        return node_by_key(self.consolidation_group, self.disposed_entity)

    def _resolve_disposer(self):
        """Pre-fill a blank disposer with the disposed node's tree parent: the
        one default, named on the field. No node, or a root without a parent:
        left blank for the mandatory check (the form offers the same
        pre-fill, business_disposal.js)."""
        if self.get("disposer_node"):
            return
        node = self._disposed_node()
        if node and node.parent_consolidation_group:
            self.disposer_node = node.parent_consolidation_group

    def _validate_disposer(self):
        """The Ownership Period's owner rule (§1 refusal 1), in this
        document's name and naming its field: the disposer is a node, not the
        disposed node, under the same root. Blank is the mandatory check's."""
        if not self.get("disposer_node"):
            return
        sentence = owner_refusal(self.disposer_node, self._disposed_node(), self.disposed_entity,
                                 field="Disposer Node")
        if sentence:
            frappe.throw(_PREFIX + sentence)

    def _declared_methods(self, node_name):
        """Every method the node declares for this deal's entity (all
        methods: the rule keeps only the latest one effective by the date)."""
        return frappe.get_all(
            "Consolidation Method Declaration",
            filters={"parent": node_name, "parenttype": "Consolidation Group", "data_area_id": self.disposed_entity},
            fields=["consolidation_method", "effective_date", "end_date"],
            order_by="effective_date desc",
            limit_page_length=0,
        )

    def _ancestor_group_nodes(self, root_name):
        """(group, own node name) of every group above this one: walk
        parent_consolidation_group from the group's root to the top. A
        sub-group's entity is also reached through its ancestors' policy."""
        seen, found, node = {root_name}, [], root_name
        while node:
            row = frappe.db.get_value("Consolidation Group", node,
                                      ["parent_consolidation_group"], as_dict=True)
            parent = row and row.get("parent_consolidation_group")
            if not parent or parent in seen:
                break
            seen.add(parent)
            above = frappe.db.get_value("Consolidation Group", parent, ["consolidation_group"], as_dict=True)
            group = above and above.get("consolidation_group")
            own = group and frappe.db.get_value(
                "Consolidation Group", {"consolidation_group": group, "data_area_id": _BLANK}, "name")
            if own:
                found.append((group, own))
            node = parent
        return found

    def _step_window_start(self):
        """The first day of the fiscal period before the deal's period (the
        non-Closing one ending the day before it starts); the deal's own
        period start when none is declared; None when no period covers the
        date (refused elsewhere)."""
        sql = ("SELECT p.start_date, p.period_type FROM `tabEPM Fiscal Year Period` p "
               "JOIN `tabEPM Fiscal Year` y ON y.name = p.parent "
               "WHERE p.parentfield = 'periods' AND p.period_type <> 'Closing' "
               "AND p.start_date <= %(d)s AND p.end_date >= %(d)s "
               "ORDER BY (p.period_type <> 'Regular'), p.start_date LIMIT 1")
        current = frappe.db.sql(sql, {"d": getdate(self.disposal_date)}, as_dict=True)
        if not current:
            return None
        start = getdate(current[0]["start_date"])
        previous = frappe.db.sql(sql, {"d": start - datetime.timedelta(days=1)}, as_dict=True)
        return getdate(previous[0]["start_date"]) if previous else start

    def _joint_operation_problem(self, root_name):
        """Refuse a deal into / out of a joint operation by name (konsol#117
        pair 3, review finding D): IFRS 11.21A is not implemented. Reads the
        entity's Ownership Periods in this group, draft or submitted, of any
        owner, and the group's Consolidation Method Declarations; the rule is
        konsol.joint_operation_model. dbt's assert_acquisition_accounts_declared
        / assert_disposal_accounts_declared are the backstop for anything this
        check cannot see."""
        periods = [row for row in frappe.get_all(
            "Ownership Period",
            filters={"consolidation_group": self.consolidation_group, "data_area_id": self.disposed_entity,
                     "docstatus": ["<", 2], "consolidation_method": "proportional",
                     "effective_date": ["<=", self.disposal_date]},
            fields=["name", "ownership_pct", "effective_date", "end_date", "docstatus"],
            order_by="effective_date desc",
            limit_page_length=0,
        )]
        declarations = [(self.consolidation_group, self._declared_methods(root_name))]
        for group, node in self._ancestor_group_nodes(root_name):
            declarations.append((group, self._declared_methods(node)))
        sentence = joint_operation_problem(self.disposed_entity, self.disposal_date, "a disposal of",
                                           "a disposal shortly after a joint operation ended", self._step_window_start(), periods, declarations)
        if sentence:
            frappe.throw(_PREFIX + sentence)

    def _link_filters(self):
        """The disposer's link into the disposed node: the filters the period
        lookups below share. A blank disposer (a disposal saved before the
        field existed, or in a host test) matches any owner, as before."""
        filters = {"consolidation_group": self.consolidation_group,
                   "data_area_id": self.disposed_entity}
        if self.get("disposer_node"):
            filters["owner_node"] = self.disposer_node
        return filters

    @staticmethod
    def _rate_to_group(group_currency, period):
        """Closing rate to the group currency at the disposal period, as the
        model's callback: 1.0 for the group currency, None when no approved
        Closing Group Exchange Rate exists (the model refuses by name)."""
        def rate(currency):
            if not currency or currency == group_currency:
                return 1.0
            rows = frappe.get_all(
                "Group Exchange Rate",
                filters={"docstatus": 1, "from_currency": currency, "to_currency": group_currency,
                         "fiscal_year": period["fiscal_year"], "fiscal_period": period["fiscal_period"],
                         "rate_type": "Closing"},
                fields=["quote", "quoted_per"],
                limit_page_length=1,
                order_by="modified desc",
            )
            if not rows:
                return None
            return true_rate(rows[0].quote, rows[0].quoted_per)
        return rate

    def _holding(self):
        """The holding being disposed of, as ``{name, ownership_pct,
        is_disposal, disposal_date}`` or None: the submitted period this
        disposal already links (a migrated disposal links its source period;
        an amendment carries its original's link) wins over the one found by
        date. ``validate`` measures against it and ``on_submit`` closes it —
        the same period, so the sentences a person reads and the period the
        approval writes agree."""
        return self._linked_period() or self._current_holding()

    def _current_holding(self):
        """The disposer's submitted Ownership Period of the entity covering
        the disposal date (the latest effective_date at or before it, still
        open or ending on or after it), as a dict, or None: that is the
        holding being disposed of. Another owner's link is another holding
        (#117 pair 1)."""
        rows = frappe.get_all(
            "Ownership Period",
            filters={**self._link_filters(), "docstatus": 1,
                     "effective_date": ["<=", self.disposal_date]},
            fields=["name", "ownership_pct", "effective_date", "end_date", "is_disposal", "disposal_date"],
            order_by="effective_date desc",
            limit_page_length=1,
        )
        if not rows:
            return None
        row = rows[0]
        if row.end_date and getdate(row.end_date) < getdate(self.disposal_date):
            return None
        return {"name": row.name, "ownership_pct": row.ownership_pct,
                "is_disposal": row.is_disposal, "disposal_date": row.disposal_date}

    def _closed_holding_problems(self, holding):
        """One holding, one disposal (PR #202 second review B2). A period an
        APPROVED Business Disposal already closed is not sold again by another
        document: that disposal is cancelled or amended instead. A period
        closed with no document behind it (``is_disposal`` set before Business
        Disposal existed) is sold only by the Draft the migration linked to it
        — that Draft IS the record of the closure, so a link to the period
        marks this document as its own closure's record; the same link is what
        an approved disposal and its amendment carry. A fresh disposal of such
        a period is refused: two documents for one sale."""
        if not holding:
            return []
        other = self._other_disposal_of(holding["name"])
        if other:
            return [
                f"{_PREFIX}{holding['name']} is already closed by Business Disposal {other}. "
                "Cancel that disposal first, or amend it."
            ]
        if int(holding.get("is_disposal") or 0) and self.get("ownership_period") != holding["name"]:
            return [
                f"{_PREFIX}{holding['name']} is already closed (disposed of on "
                f"{holding.get('disposal_date')}) and no Business Disposal records it; "
                "submit the Business Disposal migrated for that closure instead of a new one."
            ]
        return []

    def _other_disposal_of(self, period_name):
        """The name of an approved Business Disposal other than this document
        that links ``period_name``, or None."""
        filters = {"ownership_period": period_name, "docstatus": 1}
        if self.name:
            filters["name"] = ["!=", self.name]
        return frappe.db.get_value("Business Disposal", filters, "name")

    @staticmethod
    def _is_published_leaf(account):
        row = frappe.db.get_value("Main Account", account, ["status", "is_group"])
        return bool(row) and row[0] == "Published" and not row[1]

    # -- what the approval does ----------------------------------------------

    def _close_ownership_period(self):
        """The disposal ends the entity's Ownership Period on the disposal
        date (design 2a) and carries the disposal's figures onto it. Those
        fields are set only from here (``frappe.flags.from_business_combination``
        lets them through the Ownership Period's guard); a submitted period is
        written with ``db_set`` and re-synced itself.

        The period this disposal already links (a migrated disposal links its
        source period) wins over the one found by date; a blank or stale link
        falls back to the current holding (``_holding``)."""
        holding = self._holding()
        if not holding:
            frappe.throw(
                f"{_PREFIX}{self.disposed_entity} has no Ownership Period in "
                f"{self.consolidation_group} covering {self.disposal_date} any more; "
                "save the disposal again."
            )
        fields = {
            "end_date": self.disposal_date,
            "is_disposal": 1,
            "disposal_date": self.disposal_date,
            "disposal_price": float(self.get("total_proceeds") or 0),
        }
        frappe.flags.from_business_combination = True
        try:
            period = frappe.get_doc("Ownership Period", holding["name"])
            # Record what the period held BEFORE the close, so a cancel can
            # give it back exactly that (it may already have carried an end
            # date: an ownership step planned after the disposal date).
            previous = {theirs: period.get(theirs) for theirs in _REOPEN_FIELDS.values()}
            if self._records_the_closure_of(period):
                # The period is already closed and this document IS its
                # record (a Disposal the migration made for a closure the
                # legacy figures wrote, PR #202 third review 1). Cancel means
                # the sale did not happen, so what it gives back is the OPEN
                # holding — not the closure this approval merely re-states.
                previous = dict.fromkeys(_REOPEN_FIELDS.values())
            for mine, theirs in _REOPEN_FIELDS.items():
                self.db_set(mine, _period_value(theirs, previous[theirs]))
            for field, value in fields.items():
                period.db_set(field, value)
            sync_doctype_after_commit("Ownership Period", period.CH_TABLE, period.CH_FIELD_MAP)
        finally:
            frappe.flags.from_business_combination = False
        self.db_set("ownership_period", period.name)

    def _records_the_closure_of(self, period):
        """True when ``period`` is already closed (``is_disposal``) and this
        document is the one recorded against that closure: it links the period
        and no OTHER approved Business Disposal does (``validate`` refuses a
        second one; ``_closed_holding_problems``). Its cancel then reopens the
        holding instead of writing the closure back."""
        if not int(period.get("is_disposal") or 0):
            return False
        if self.get("ownership_period") != period.name:
            return False
        return not self._other_disposal_of(period.name)

    def _linked_period(self):
        """The Ownership Period this disposal links (``ownership_period``), as
        ``{name, ownership_pct, is_disposal, disposal_date}``, when it still
        exists and is submitted (only a submitted period is a holding, as the
        Business Combination reads it); a blank, deleted, Draft or cancelled
        link → None (the current holding is used)."""
        name = self.get("ownership_period")
        if not name:
            return None
        rows = frappe.get_all(
            "Ownership Period",
            filters={"name": name, "docstatus": 1},
            fields=["name", "ownership_pct", "is_disposal", "disposal_date"],
            limit_page_length=1,
        )
        if not rows:
            return None
        row = rows[0]
        return {"name": row.name, "ownership_pct": row.ownership_pct,
                "is_disposal": row.is_disposal, "disposal_date": row.disposal_date}

    def _assert_no_later_ownership_period(self):
        """Reopening the closed period under a LATER submitted Ownership
        Period of the same node would make the two overlap (the disposal
        ended the holding; a later period is a fresh one). The later period
        is cancelled first — by whoever decides the disposal never happened."""
        later = frappe.get_all(
            "Ownership Period",
            filters={**self._link_filters(), "docstatus": 1,
                     "effective_date": [">", self.disposal_date]},
            fields=["name"],
            order_by="effective_date asc",
        )
        if later:
            frappe.throw(
                f"{_PREFIX}Cancel the later Ownership Period(s) first: "
                + ", ".join(row.name for row in later)
            )

    def _reopen_ownership_period(self):
        """Cancelling the approval undoes what ``_close_ownership_period`` did:
        the linked Ownership Period gets back exactly the values the approval
        recorded in the Reopen Data section (its end date, disposal flag, date
        and price as they stood before the close), so a period that already
        ended after the disposal date ends there again rather than being left
        open. Same flag, same ``db_set`` route, its own re-sync. A period
        deleted since is nothing to reopen: skip, do not throw (the cancel
        must still go through)."""
        name = self.get("ownership_period")
        if not name or not frappe.db.exists("Ownership Period", name):
            return
        fields = {
            theirs: _period_value(theirs, self.get(mine))
            for mine, theirs in _REOPEN_FIELDS.items()
        }
        frappe.flags.from_business_combination = True
        try:
            period = frappe.get_doc("Ownership Period", name)
            for field, value in fields.items():
                period.db_set(field, value)
            sync_doctype_after_commit("Ownership Period", period.CH_TABLE, period.CH_FIELD_MAP)
        finally:
            frappe.flags.from_business_combination = False

    # -- warehouse -----------------------------------------------------------

    def _sync(self):
        """Header and the child table, once, after the commit."""
        sync_doctype_after_commit(self.doctype, self.CH_TABLE, self.CH_FIELD_MAP)
        for doctype, controller in self.CHILD_CONTROLLERS.items():
            sync_doctype_after_commit(doctype, controller.CH_TABLE, controller.CH_FIELD_MAP)

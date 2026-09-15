"""Business Combination: one document per acquisition of control (konsolidat#198).

Three layers, kept apart on purpose (design 2a):

* the **deal is a declared input** — this document's lines: what was paid,
  the balance sheet acquired, the costs of acquiring it;
* the **policy is configured**, once, on the Consolidation Group node named
  by ``consolidation_group`` (the group root carries the Consolidation
  Policy and the declared accounts, design 1/1a); the deal only keeps a
  read-only copy of the NCI measurement it was measured under;
* the **mechanics are programmed** in the pure, host-tested
  ``konsol.business_combination_model``: this controller looks the facts up
  (the declared period the acquisition date falls in, the period's Closing
  group rates, the chart, the entity's trial balances) and feeds them in.

Lifecycle: ``validate`` computes the Result fields and throws the model's
sentences; ``before_submit`` re-checks the period is open and the required
accounts are declared; ``on_submit`` creates (or links) the Ownership Period
the acquisition starts and writes the approved deal to the warehouse; an
amendment is allowed only inside the policy's 12-month measurement period.
Submit = approval (the workflow); cancel only while the acquisition period and
every period the holding covers are open and no approved Business Disposal
has sold the holding since, and it undoes the approval's Ownership Period:
cancelled again when this deal created it, its deal fields cleared when it
pre-existed. Every refusal speaks of this deal by name. Nothing here saves
the document from a hook or commits.
"""
import frappe
from frappe.model.document import Document
from frappe.utils import add_months, getdate, nowdate

from konsol.business_combination_model import problems, totals
from konsol.clickhouse import sync_doctype_after_commit
from konsol.consolidation.doctype.business_combination_acquired_balance.business_combination_acquired_balance import (  # noqa: E501
    BusinessCombinationAcquiredBalance,
)
from konsol.consolidation.doctype.business_combination_consideration.business_combination_consideration import (  # noqa: E501
    BusinessCombinationConsideration,
)
from konsol.consolidation.doctype.business_combination_cost.business_combination_cost import (
    BusinessCombinationCost,
)
from konsol.consolidation_policy_model import (
    ACCOUNT_FIELDS,
    POLICY_FIELDS,
    account_problems,
    required_accounts,
)
from konsol.group_rates import true_rate
from konsol.period_status import PeriodNotDeclared, assert_open, assert_open_between

#: The Result fields ``validate`` fills from the model, in the form's order.
RESULT_FIELDS = (
    "total_consideration",
    "net_assets_acquired",
    "fair_value_adjustments",
    "goodwill",
    "bargain_purchase_gain",
    "nci_at_acquisition",
    "costs_expensed",
)

#: A blank Link is stored as NULL: match both spellings (see Ownership Period).
_BLANK = ["is", "not set"]

#: What ``_ensure_ownership_period`` writes onto a pre-existing period, and
#: the blanks ``_undo_ownership_period`` gives it back (in the same order).
_DEAL_FIELD_RESET = {
    "acquisition_date": None,
    "is_first_acquisition": 0,
    "acquisition_price": 0,
    "fair_value_adjustment": 0,
}

_PREFIX = "Business Combination: "


class BusinessCombination(Document):
    CH_TABLE = "epm_staging.business_combinations"
    CH_FIELD_MAP = {
        "name": "name",
        "consolidation_group": "consolidation_group",
        "acquired_entity": "acquired_entity",
        "acquisition_date": "acquisition_date",
        "share_acquired_pct": "share_acquired_pct",
        "consideration_currency": "consideration_currency",
        "total_consideration": "total_consideration",
        "net_assets_acquired": "net_assets_acquired",
        "fair_value_adjustments": "fair_value_adjustments",
        "goodwill": "goodwill",
        "bargain_purchase_gain": "bargain_purchase_gain",
        "nci_at_acquisition": "nci_at_acquisition",
        "ownership_period": "ownership_period",
    }
    #: child doctype -> its controller (CH_TABLE / CH_FIELD_MAP); each child
    #: table is its own doctype in the warehouse, keyed (parent, idx).
    CHILD_CONTROLLERS = {
        "Business Combination Consideration": BusinessCombinationConsideration,
        "Business Combination Acquired Balance": BusinessCombinationAcquiredBalance,
        "Business Combination Cost": BusinessCombinationCost,
    }

    # -- lifecycle -----------------------------------------------------------

    def validate(self):
        period = self._acquisition_period()
        root = self._root()
        self.nci_measurement = root.get("goodwill_method") or ""

        rate_to_group = self._rate_to_group(root.get("reporting_currency"), period)
        try:
            result = totals(self, self._lines("consideration"), self._lines("acquired_balances"),
                            self._lines("costs"), root, rate_to_group, self._is_equity)
        except ValueError as e:  # a consideration or cost currency with no Closing rate
            frappe.throw(
                f"{_PREFIX}{e} ({period['period_code']} of FY{period['fiscal_year']}): "
                f"approve a Closing Group Exchange Rate to {root.get('reporting_currency')} for it."
            )
        for field in RESULT_FIELDS:
            setattr(self, field, float(result[field]))

        facts = {
            "has_tb_at_or_before": self._has_tb_at_or_before(period),
            "totals": result,
            "is_equity": self._is_equity,
            "is_published_leaf": self._is_published_leaf,
        }
        found = problems(self, self._lines("consideration"), self._lines("acquired_balances"),
                         self._lines("costs"), root, facts)
        found += self._amendment_problems(root)
        if found:
            frappe.throw("<br>".join(found))

    def before_submit(self):
        """Approval: the acquisition period is still open, and the accounts
        this deal posts to are still declared on the root (it may have
        changed since the deal was saved)."""
        period = self._acquisition_period()
        assert_open(period["fiscal_year"], period["fiscal_period"],
                    action="approve a business combination")
        root = self._root()
        found = account_problems(root, required_accounts(root, self._deal()), self._is_published_leaf)
        if found:
            frappe.throw("<br>".join(found))

    def on_submit(self):
        self._ensure_ownership_period()
        self._sync()

    def before_cancel(self):
        period = self._acquisition_period()
        assert_open(period["fiscal_year"], period["fiscal_period"],
                    action="cancel a business combination")
        self._assert_not_sold()
        self._assert_holding_span_open()

    def _assert_holding_span_open(self):
        """Undoing the approval cancels or clears the Ownership Period it
        started, and that changes every period from the acquisition date to
        the period's end — today, while it is open-ended. Gating the
        acquisition period alone let the cancel run into the period's own
        ``before_cancel``, whose refusal speaks of "an ownership period" and
        never of the deal the person is cancelling (PR #202 second review
        B5); the same span check runs here first, in this deal's name. Only
        a submitted linked period is undone, so only then is there a span."""
        name = self.get("ownership_period")
        if not name:
            return
        linked = frappe.db.get_value(
            "Ownership Period", {"name": name, "docstatus": 1}, ["name", "end_date"], as_dict=True
        )
        if not linked:
            return
        assert_open_between(self.acquisition_date, linked.get("end_date") or nowdate(),
                            action=f"cancel Business Combination {self.name}")

    def _assert_not_sold(self):
        """An approved Business Disposal that links this deal's Ownership
        Period sold the holding this deal acquired: undoing the acquisition
        under it would leave a disposal of nothing (PR #202 second review B3).
        The disposal is cancelled first. Guarded by ``table_exists`` as the
        group's ``has_deals`` is: the Disposal doctype may not be installed
        yet on a stack cancelling an older deal mid-migrate."""
        name = self.get("ownership_period")
        if not name or not frappe.db.table_exists("Business Disposal"):
            return
        sold_by = frappe.db.get_value(
            "Business Disposal", {"ownership_period": name, "docstatus": 1}, "name"
        )
        if sold_by:
            frappe.throw(f"{_PREFIX}Business Disposal {sold_by} sold this holding. Cancel it first.")

    def on_cancel(self):
        self._undo_ownership_period()
        self._sync()

    def after_delete(self):
        """after_delete, not on_trash: on_trash runs before the row is gone,
        so the full-table re-send put it straight back (#120)."""
        self._sync()

    # -- facts the model needs ---------------------------------------------

    def _lines(self, table):
        return list(self.get(table) or [])

    def _acquisition_period(self):
        """The declared fiscal period the acquisition date falls in (konsol#189:
        periods are declared rows of an EPM Fiscal Year, never ``date.month``).
        A Regular period wins over an Opening/Closing/Adjustment period that
        shares the date. No period → refused."""
        rows = frappe.db.sql(
            "SELECT y.fiscal_year, p.fiscal_period, p.period_code, p.period_type "
            "FROM `tabEPM Fiscal Year Period` p "
            "JOIN `tabEPM Fiscal Year` y ON y.name = p.parent "
            "WHERE p.parentfield = 'periods' AND p.start_date <= %(d)s AND p.end_date >= %(d)s "
            "ORDER BY (p.period_type <> 'Regular'), p.start_date LIMIT 1",
            {"d": getdate(self.acquisition_date)},
            as_dict=True,
        )
        if not rows:
            frappe.throw(
                f"{_PREFIX}no declared fiscal period covers {self.acquisition_date}: "
                "declare it in EPM Fiscal Year before recording the acquisition.",
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
        the reporting currency the Result is measured in."""
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
                "entity: the group root carries the Consolidation Policy the deal is measured under."
            )
        return root

    @staticmethod
    def _rate_to_group(group_currency, period):
        """Closing rate to the group currency at the acquisition period, as the
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
            )
            if not rows:
                return None
            return true_rate(rows[0].quote, rows[0].quoted_per)
        return rate

    def _has_tb_at_or_before(self, period):
        """A submitted trial balance of the entity at or before the acquisition
        period: then the acquired balance sheet may be left to the model."""
        base = {"data_area_id": self.acquired_entity, "docstatus": 1}
        return bool(
            frappe.db.exists("Trial Balance Submission",
                             {**base, "fiscal_year": ["<", period["fiscal_year"]]})
            or frappe.db.exists("Trial Balance Submission",
                                {**base, "fiscal_year": period["fiscal_year"],
                                 "fiscal_period": ["<=", period["fiscal_period"]]})
        )

    @staticmethod
    def _is_equity(account):
        """An acquired balance line is equity when the chart says so — never
        guessed from its amount."""
        return frappe.db.get_value("Main Account", account, "account_type") == "Equity"

    @staticmethod
    def _is_published_leaf(account):
        row = frappe.db.get_value("Main Account", account, ["status", "is_group"])
        return bool(row) and row[0] == "Published" and not row[1]

    def _deal(self):
        """The deal as ``required_accounts`` reads it, from the computed Result."""
        bargain = float(self.get("bargain_purchase_gain") or 0)
        return {
            "kind": "acquisition",
            "share_pct": float(self.share_acquired_pct or 0),
            "goodwill": -bargain if bargain > 0 else float(self.get("goodwill") or 0),
            "has_costs": bool(self._lines("costs")),
        }

    def _amendment_problems(self, root):
        """An amendment re-measures an approved deal: IFRS 3 allows that only
        within the measurement period, which the policy switches on (12
        months from the acquisition date) or off."""
        if not self.get("amended_from"):
            return []
        chosen = root.get("measurement_period") or "blank"
        if chosen != "12 months":
            return [
                f"{_PREFIX}amending an approved deal needs the Consolidation Policy's "
                f"Measurement Period set to 12 months on the group root; it is {chosen}."
            ]
        last_day = add_months(getdate(self.acquisition_date), 12)
        if getdate(nowdate()) > last_day:
            return [
                f"{_PREFIX}the 12-month measurement period for this acquisition ended on "
                f"{last_day}; record later changes as a Consolidation Adjustment."
            ]
        return []

    # -- what the approval creates -----------------------------------------

    def _ensure_ownership_period(self):
        """The acquisition starts an Ownership Period for the entity on the
        acquisition date (design 2a): update the period this deal already
        links, else create one or link the one already declared for that date,
        and carry the deal's figures onto it. Those fields are set only from
        here (``frappe.flags.from_business_combination`` lets them through the
        Ownership Period's guard).

        The link wins over the date: a migrated deal is dated by its source
        period's own ``acquisition_date``, which may differ from the period's
        ``effective_date``; matching by date would insert a second period
        overlapping the linked one.

        The deal owns the period it created OR submitted (``created``): a
        Draft period the approval submits has no approval behind it but this
        one, so undoing the approval cancels it again rather than leaving a
        submitted period standing with four blanked fields."""
        existing = self._linked_period() or self._period_on_the_acquisition_date()
        deal_fields = {
            "acquisition_date": self.acquisition_date,
            "is_first_acquisition": int(self._is_first_acquisition(existing)),
            "acquisition_price": float(self.get("total_consideration") or 0),
            "fair_value_adjustment": float(self.get("fair_value_adjustments") or 0),
        }
        node = {"consolidation_group": self.consolidation_group, "data_area_id": self.acquired_entity}
        created = False
        frappe.flags.from_business_combination = True
        try:
            if existing:
                period = frappe.get_doc("Ownership Period", existing["name"])
                if int(existing["docstatus"]) == 0:
                    # A Draft period the approval submits says what the
                    # approved deal says. The deal is the source of the share
                    # and the date even when the Draft was declared by hand
                    # with other values: the approval measured this deal, and
                    # the period it starts must agree with it.
                    period.update({
                        **deal_fields,
                        "ownership_pct": self.share_acquired_pct,
                        "effective_date": self.acquisition_date,
                    })
                    period.submit()
                    created = True
                else:
                    for field, value in deal_fields.items():
                        period.db_set(field, value)
                    sync_doctype_after_commit("Ownership Period", period.CH_TABLE, period.CH_FIELD_MAP)
            else:
                new_period = {
                    "doctype": "Ownership Period",
                    **node,
                    "effective_date": self.acquisition_date,
                    "ownership_pct": self.share_acquired_pct,
                    "consolidation_method": self._consolidation_method(),
                    **deal_fields,
                }
                # A period this node already had on this date and cancelled
                # since (approve → cancel → amend → approve) still holds the
                # format: name; recorded as its amendment, the new one is
                # named after it (…-1) instead of colliding with it.
                cancelled = self._cancelled_period_on_the_acquisition_date()
                if cancelled:
                    new_period["amended_from"] = cancelled
                period = frappe.get_doc(new_period)
                period.insert()
                period.submit()
                created = True
        finally:
            frappe.flags.from_business_combination = False
        self.db_set("ownership_period", period.name)
        if created:
            # Remembered so a cancel knows whether to cancel the period
            # again or merely clear the figures it lent a pre-existing one.
            self.db_set("created_ownership_period", 1)

    def _undo_ownership_period(self):
        """Cancelling the approval undoes what ``_ensure_ownership_period``
        did to the linked Ownership Period. A period this deal CREATED or
        SUBMITTED is cancelled again (its own ``before_cancel`` keeps the
        closed-period gate, its own ``on_cancel`` re-syncs it); a period that
        was already submitted and only received the deal's figures keeps
        standing and gets the four deal fields cleared, with its own re-sync.
        Both under the flag the period's guard reads. No link, or a period
        deleted, Draft or already cancelled since: nothing to undo, skip (the
        cancel must still go through)."""
        name = self.get("ownership_period")
        if not name or not frappe.db.exists("Ownership Period", {"name": name, "docstatus": 1}):
            return
        frappe.flags.from_business_combination = True
        try:
            period = frappe.get_doc("Ownership Period", name)
            if int(self.get("created_ownership_period") or 0):
                period.cancel()
            else:
                for field, value in _DEAL_FIELD_RESET.items():
                    period.db_set(field, value)
                sync_doctype_after_commit("Ownership Period", period.CH_TABLE, period.CH_FIELD_MAP)
        finally:
            frappe.flags.from_business_combination = False

    def _consolidation_method(self):
        """A Business Combination is an acquisition of control, and control is
        presumed with more than half of the voting interest: above 50% the
        entity is fully consolidated. At 50% or below the deal records an
        investee under significant influence, so the period starts as
        equity-accounted; the method on the Ownership Period stays editable
        for the cases (potential voting rights, contractual control) the
        percentage alone does not decide."""
        return "full" if float(self.share_acquired_pct or 0) > 50 else "equity"

    def _linked_period(self):
        """The Ownership Period this deal links (``ownership_period``), as
        ``{name, docstatus}``, when it still exists and is not cancelled; else
        None (a blank or stale link falls back to the date lookup)."""
        linked = self.get("ownership_period")
        if not linked or not frappe.db.exists("Ownership Period", linked):
            return None
        return frappe.db.get_value(
            "Ownership Period",
            {"name": linked, "docstatus": ["<", 2]},
            ["name", "docstatus"],
            as_dict=True,
        )

    def _period_on_the_acquisition_date(self):
        """The entity's Ownership Period declared for the acquisition date, as
        ``{name, docstatus}``, or None."""
        return frappe.db.get_value(
            "Ownership Period",
            {"consolidation_group": self.consolidation_group, "data_area_id": self.acquired_entity,
             "effective_date": self.acquisition_date, "docstatus": ["<", 2]},
            ["name", "docstatus"],
            as_dict=True,
        )

    def _cancelled_period_on_the_acquisition_date(self):
        """The name of the latest CANCELLED Ownership Period of this node on
        the acquisition date (an earlier approval of this deal, undone), or
        None. Latest by creation, not by name: the amendments are named …-1,
        …-2, … and as text ``…-9`` sorts above ``…-10``, so the tenth
        re-approval would amend the wrong one and collide (PR #202 third
        review 3). The newest is the last created."""
        rows = frappe.get_all(
            "Ownership Period",
            filters={"consolidation_group": self.consolidation_group,
                     "data_area_id": self.acquired_entity,
                     "effective_date": self.acquisition_date, "docstatus": 2},
            fields=["name"],
            order_by="creation desc",
            limit_page_length=1,
        )
        return rows[0].name if rows else None

    def _is_first_acquisition(self, existing=None):
        """No earlier submitted Ownership Period for this entity in this group.
        The period this deal itself updates is not an earlier one, whatever
        its effective date."""
        filters = {
            "consolidation_group": self.consolidation_group,
            "data_area_id": self.acquired_entity,
            "docstatus": 1,
            "effective_date": ["<", self.acquisition_date],
        }
        if existing:
            filters["name"] = ["!=", existing["name"]]
        return not frappe.db.exists("Ownership Period", filters)

    # -- warehouse -----------------------------------------------------------

    def _sync(self):
        """Header and the three child tables, once, after the commit."""
        sync_doctype_after_commit(self.doctype, self.CH_TABLE, self.CH_FIELD_MAP)
        for doctype, controller in self.CHILD_CONTROLLERS.items():
            sync_doctype_after_commit(doctype, controller.CH_TABLE, controller.CH_FIELD_MAP)

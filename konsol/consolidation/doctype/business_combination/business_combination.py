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
Submit = approval (the workflow); cancel only while the acquisition period is
open. Nothing here saves the document from a hook or commits.
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
from konsol.period_status import PeriodNotDeclared, assert_open

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

    def on_cancel(self):
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
        acquisition date (design 2a): create it, or link the one already
        declared for that date, and carry the deal's figures onto it. Those
        fields are set only from here (``frappe.flags.from_business_combination``
        lets them through the Ownership Period's guard)."""
        deal_fields = {
            "acquisition_date": self.acquisition_date,
            "is_first_acquisition": int(self._is_first_acquisition()),
            "acquisition_price": float(self.get("total_consideration") or 0),
            "fair_value_adjustment": float(self.get("fair_value_adjustments") or 0),
        }
        node = {"consolidation_group": self.consolidation_group, "data_area_id": self.acquired_entity}
        existing = frappe.db.get_value(
            "Ownership Period",
            {**node, "effective_date": self.acquisition_date, "docstatus": ["<", 2]},
            ["name", "docstatus"],
            as_dict=True,
        )
        frappe.flags.from_business_combination = True
        try:
            if existing:
                period = frappe.get_doc("Ownership Period", existing["name"])
                if int(existing["docstatus"]) == 0:
                    period.update(deal_fields)
                    period.submit()
                else:
                    for field, value in deal_fields.items():
                        period.db_set(field, value)
                    sync_doctype_after_commit("Ownership Period", period.CH_TABLE, period.CH_FIELD_MAP)
            else:
                period = frappe.get_doc({
                    "doctype": "Ownership Period",
                    **node,
                    "effective_date": self.acquisition_date,
                    "ownership_pct": self.share_acquired_pct,
                    "consolidation_method": self._consolidation_method(),
                    **deal_fields,
                })
                period.insert()
                period.submit()
        finally:
            frappe.flags.from_business_combination = False
        self.db_set("ownership_period", period.name)

    def _consolidation_method(self):
        """A Business Combination is an acquisition of control, and control is
        presumed with more than half of the voting interest: above 50% the
        entity is fully consolidated. At 50% or below the deal records an
        investee under significant influence, so the period starts as
        equity-accounted; the method on the Ownership Period stays editable
        for the cases (potential voting rights, contractual control) the
        percentage alone does not decide."""
        return "full" if float(self.share_acquired_pct or 0) > 50 else "equity"

    def _is_first_acquisition(self):
        """No earlier submitted Ownership Period for this entity in this group."""
        return not frappe.db.exists("Ownership Period", {
            "consolidation_group": self.consolidation_group,
            "data_area_id": self.acquired_entity,
            "docstatus": 1,
            "effective_date": ["<", self.acquisition_date],
        })

    # -- warehouse -----------------------------------------------------------

    def _sync(self):
        """Header and the three child tables, once, after the commit."""
        sync_doctype_after_commit(self.doctype, self.CH_TABLE, self.CH_FIELD_MAP)
        for doctype, controller in self.CHILD_CONTROLLERS.items():
            sync_doctype_after_commit(doctype, controller.CH_TABLE, controller.CH_FIELD_MAP)

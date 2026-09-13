"""Group Exchange Rate: the group's governed translation rates (konsol#103).

Decided by the user on 13 Sep 2026:

* One governed rate table in konsol, owned by group finance. The ERP feed only
  proposes (``konsol.group_rates.prefill_from_erp``); a person accepts.
* Grain: group reporting currency (to), from-currency, fiscal year, fiscal
  period, rate type (Closing / Average). Only rates into a group reporting
  currency are entered; cross rates are derived. Historical equity rates stay
  in Historical Equity Rate.
* Submit is the approval (12 Sep conventions): EPM Analyst drafts, EPM Admin
  submits. Cancel only while the period is open; a cancelled rate leaves the
  warehouse and stays here as the audit trail.
* Rates lock when their period closes: submit and cancel are refused, so a
  change is a reopen of the period (Period Status keeps who and when), a
  cancel, and an amendment that must say why.

Submitted rows are written through to ``epm_staging.group_exchange_rates``,
which gold_consolidated_trial_balance translates from.
"""
import frappe
from frappe.model.document import Document
from frappe.model.naming import make_autoname

from konsol.clickhouse import sync_doctype_after_commit
from konsol.period_status import assert_open

RATE_TYPES = ("Closing", "Average")
ADOPTION_SOURCE = "Adoption"
PREFILL_SOURCE = "ERP pre-fill"
MANUAL_SOURCE = "Manual"
GRAIN = ("to_currency", "from_currency", "fiscal_year", "fiscal_period", "rate_type")


class GroupExchangeRate(Document):
    CH_TABLE = "epm_staging.group_exchange_rates"
    CH_FIELD_MAP = {
        "to_currency": "to_currency",
        "from_currency": "from_currency",
        "fiscal_year": "fiscal_year",
        "fiscal_period": "fiscal_period",
        "rate_type": "rate_type",
        "rate": "rate",
        "document": "name",
    }

    def autoname(self):
        """Readable and unique: the grain, then a counter for the rare second
        draft of one key. An amendment is named by Frappe (`<name>-1`)."""
        self.name = make_autoname(
            f"GER-{int(self.fiscal_year)}-{int(self.fiscal_period):02d}-"
            f"{self.from_currency}-{self.to_currency}-{self.rate_type}-.##",
            doc=self,
        )

    # -- validation ---------------------------------------------------------

    def validate(self):
        from konsol import group_rates

        if self.rate_type not in RATE_TYPES:
            frappe.throw(f"Rate Type must be one of {', '.join(RATE_TYPES)}.", frappe.ValidationError)
        if self.from_currency and self.from_currency == self.to_currency:
            frappe.throw(
                f"A rate from {self.from_currency} into itself is 1 by definition; "
                "it is not entered.", frappe.ValidationError)
        self._validate_period()
        self._validate_rate(group_rates)
        self._validate_group_currency()
        self._track_source()
        if self.amended_from and not (self.change_reason or "").strip():
            frappe.throw(
                "Say why this rate replaces the one it amends (Reason for Change). "
                "The cancelled rate, the period's reopen and this reason are the audit trail.",
                frappe.MandatoryError)

    def _validate_period(self):
        year, period = int(self.fiscal_year or 0), self.fiscal_period
        # ClickHouse Date holds 1970-01-01..2149-06-06 and clamps silently.
        if not 1970 <= year <= 2148:
            frappe.throw(f"Fiscal Year {self.fiscal_year} is out of range.", frappe.ValidationError)
        if period in (None, "") or not frappe.db.exists("Fiscal Period", {"fiscal_period": int(period)}):
            frappe.throw(f"No Fiscal Period numbered {period}.", frappe.ValidationError)

    def _validate_rate(self, group_rates):
        """A positive, true rate of plausible magnitude (#138): a fat-fingered
        93.78 for 0.9378 is refused here, not found months later."""
        rate = float(self.rate or 0)
        if rate <= 0:
            frappe.throw("Rate must be a positive number.", frappe.ValidationError)
        problem = group_rates.magnitude_problem(self.from_currency, self.to_currency, rate)
        if problem:
            frappe.throw(problem, frappe.ValidationError)

    def _validate_group_currency(self):
        """Only rates INTO a group reporting currency are entered; a rate into
        any other currency would translate nothing."""
        if not self.to_currency:
            return
        if not frappe.db.sql(
            "SELECT 1 FROM `tabConsolidation Group` WHERE reporting_currency = %s "
            "AND (is_group = 1 OR IFNULL(data_area_id, '') = '') LIMIT 1",
            (self.to_currency,),
        ):
            frappe.throw(
                f"{self.to_currency} is not the reporting currency of any consolidation group. "
                "Enter rates into a group's reporting currency only; cross rates are derived.",
                frappe.ValidationError)

    def _track_source(self):
        """A pre-filled draft whose rate a person changed is a manual rate."""
        if self.source == PREFILL_SOURCE and self.erp_rate and \
                abs(float(self.rate) - float(self.erp_rate)) > 1e-12:
            self.source = MANUAL_SOURCE
        if not self.source:
            self.source = MANUAL_SOURCE

    # -- lifecycle ----------------------------------------------------------

    def _adopting(self):
        """The one-time adoption (konsol#103 upgrade) may submit into a closed
        period: it records the rate that period was already translated at.
        Only the system, only rows labelled as adopted, only inside the run."""
        return bool(frappe.flags.get("konsol_adopting_rates")) and self.source == ADOPTION_SOURCE \
            and frappe.session.user == "Administrator"

    def before_submit(self):
        """Submit is the approval, and a closed period's rates are locked."""
        if not self._adopting():
            assert_open(self.fiscal_year, self.fiscal_period, action="approve a group exchange rate")
        self._refuse_second_approved_rate()

    def _refuse_second_approved_rate(self):
        """One approved rate per grain. A locking read, so two approvals of the
        same key in parallel cannot both pass (REPEATABLE READ; indexed below)."""
        other = frappe.db.sql(
            "SELECT name FROM `tabGroup Exchange Rate` WHERE to_currency = %s AND from_currency = %s "
            "AND fiscal_year = %s AND fiscal_period = %s AND rate_type = %s AND docstatus = 1 "
            "AND name != %s FOR UPDATE",
            tuple(self.get(f) for f in GRAIN) + (self.name,),
        )
        if other:
            frappe.throw(
                f"{other[0][0]} is already the approved {self.rate_type} rate {self.from_currency} → "
                f"{self.to_currency} for FY{self.fiscal_year} P{self.fiscal_period}. Cancel it "
                "(while the period is open) and amend it instead.",
                frappe.DuplicateEntryError)

    def before_cancel(self):
        """Cancel only while the period is open (12 Sep conventions)."""
        assert_open(self.fiscal_year, self.fiscal_period, action="cancel a group exchange rate")

    def on_submit(self):
        sync_doctype_after_commit(self.doctype, self.CH_TABLE, self.CH_FIELD_MAP)

    def on_cancel(self):
        sync_doctype_after_commit(self.doctype, self.CH_TABLE, self.CH_FIELD_MAP)

    def on_trash(self):
        """A cancelled rate is the record of what a period was once translated
        at; it is kept. Drafts may be deleted."""
        if self.docstatus == 2:
            frappe.throw("A cancelled group exchange rate is kept as the audit trail.",
                         frappe.PermissionError)


def on_doctype_update():
    """The grain's index: the approval check's locking read uses it, and the
    translation's lookups follow the same key."""
    frappe.db.add_index("Group Exchange Rate", list(GRAIN), index_name="grain")

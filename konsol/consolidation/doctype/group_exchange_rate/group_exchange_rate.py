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

A rate is "units of the group currency per 1 unit of the from-currency". A
small one (KRW -> USD 0.00074) loses digits at MariaDB's 9 decimal places, so
it is quoted the other way round (1350 KRW per USD) with ``inverse_quote``
ticked, and the warehouse inverts it.

Submitted rows are written through to ``epm_staging.group_exchange_rates``,
which gold_consolidated_trial_balance translates from.
"""
import frappe
from frappe.model.document import Document

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
        "inverse_quote": "inverse_quote",
    }

    def autoname(self):
        """Readable and unique: the grain, then a counter for the rare second
        draft of one key. An amendment is named by Frappe (`<name>-1`)."""
        from frappe.model.naming import make_autoname

        self.name = make_autoname(
            f"GER-{int(self.fiscal_year)}-{int(self.fiscal_period):02d}-"
            f"{self.from_currency}-{self.to_currency}-{self.rate_type}-.##",
            doc=self,
        )

    # -- validation ---------------------------------------------------------

    def validate(self):
        self._validate_grain()
        self._validate_period()
        self._guard_provenance()
        self._validate_rate()
        self._validate_group_currency()
        self._track_source()
        self._require_reasons()

    def _saved_version(self):
        """The saved version of this document, or None for a new one."""
        if self.is_new():
            return None
        return self.get_doc_before_save()

    def _validate_grain(self):
        if self.rate_type not in RATE_TYPES:
            frappe.throw(f"Rate Type must be one of {', '.join(RATE_TYPES)}.", frappe.ValidationError)
        if self.from_currency and self.from_currency == self.to_currency:
            frappe.throw(
                f"A rate from {self.from_currency} into itself is 1 by definition; "
                "it is not entered.", frappe.ValidationError)

    def _validate_period(self):
        year, period = int(self.fiscal_year or 0), self.fiscal_period
        # ClickHouse Date holds 1970-01-01..2149-06-06 and clamps silently.
        if not 1970 <= year <= 2148:
            frappe.throw(f"Fiscal Year {self.fiscal_year} is out of range.", frappe.ValidationError)
        if period in (None, "") or not frappe.db.exists("Fiscal Period", {"fiscal_period": int(period)}):
            frappe.throw(f"No Fiscal Period numbered {period}.", frappe.ValidationError)

    def _guard_provenance(self):
        """Source and ERP Quote are read-only in the form only; REST writes any
        field. "Adoption" is set by the one-time adoption alone, "ERP pre-fill"
        and the ERP Quote by prefill_from_erp alone (each sets its own flag), so
        neither label can be forged onto a rate a person typed."""
        before = self._saved_version()
        was = before.source if before else None
        prefilling = bool(frappe.flags.get("konsol_prefilling_rates"))
        if self.source == ADOPTION_SOURCE and was != ADOPTION_SOURCE and not self._adopting():
            frappe.throw('Source "Adoption" is set only by the one-time rate adoption (konsol#103).',
                         frappe.PermissionError)
        if self.source == PREFILL_SOURCE and was != PREFILL_SOURCE and not prefilling:
            frappe.throw('Source "ERP pre-fill" is set only by Pre-fill from ERP.', frappe.PermissionError)
        erp_before = float(before.erp_rate or 0) if before else 0.0
        if abs(float(self.erp_rate or 0) - erp_before) > 1e-12 and not (prefilling or self._adopting()):
            frappe.throw("ERP Quote is recorded by Pre-fill from ERP only.", frappe.PermissionError)

    def _validate_rate(self):
        """A positive, true rate of plausible magnitude (#138): a fat-fingered
        93.78 for 0.9378 is refused here, not found months later."""
        from konsol import group_rates

        rate = float(self.rate or 0)
        if rate <= 0:
            frappe.throw("Rate must be a positive number.", frappe.ValidationError)
        if rate < group_rates.MIN_QUOTE:
            flipped = f"{self.to_currency} per 1 {self.from_currency}" if self.inverse_quote else \
                f"{self.from_currency} per 1 {self.to_currency}"
            frappe.throw(
                f"{rate:.9g} is below {group_rates.MIN_QUOTE:g} and loses digits at 9 decimal places. "
                f"Quote it the other way round ({flipped}, {1 / rate:,.6f}) and "
                f"{'untick' if self.inverse_quote else 'tick'} Inverse Quote.", frappe.ValidationError)
        problem = group_rates.magnitude_problem(
            self.from_currency, self.to_currency, group_rates.effective_rate(rate, self.inverse_quote))
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
        """A pre-filled draft whose rate or direction a person changed is a
        manual rate. The ERP Quote follows the direction, so it stays
        comparable with the rate."""
        before = self._saved_version()
        if before is not None and int(before.inverse_quote or 0) != int(self.inverse_quote or 0) \
                and self.erp_rate:
            self.erp_rate = round(1.0 / float(self.erp_rate), 9)
            if self.source == PREFILL_SOURCE:
                self.source = MANUAL_SOURCE
        if self.source == PREFILL_SOURCE and self.erp_rate and \
                abs(float(self.rate) - float(self.erp_rate)) > 1e-12:
            self.source = MANUAL_SOURCE
        if not self.source:
            self.source = MANUAL_SOURCE

    def _require_reasons(self):
        """Say why: always for an amendment, and for a move over 50% from the
        previous approved rate for this key or from the ERP quote."""
        from konsol import group_rates

        if (self.change_reason or "").strip():
            return
        if self.amended_from:
            frappe.throw(
                "Say why this rate replaces the one it amends (Reason for Change). "
                "The cancelled rate, the period's reopen and this reason are the audit trail.",
                frappe.MandatoryError)
        if self._adopting():
            return  # the rate a period was already translated at; the note says so
        rate = group_rates.effective_rate(self.rate, self.inverse_quote)
        previous = group_rates.previous_approved(
            self.to_currency, self.from_currency, self.rate_type, self.fiscal_year, self.fiscal_period)
        erp = group_rates.effective_rate(self.erp_rate, self.inverse_quote) if self.erp_rate else None
        problem = group_rates.move_problem(rate, previous, erp)
        if problem:
            frappe.throw(problem, frappe.MandatoryError)

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

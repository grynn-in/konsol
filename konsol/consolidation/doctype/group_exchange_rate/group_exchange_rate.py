"""Group Exchange Rate: the group's governed translation rates (konsol#103).

Decided by the user on 13 Sep 2026:

* One source of truth for FX rates, and it comes via konsol. Group finance
  owns this table; the ERP feed only proposes
  (``konsol.group_rates.prefill_from_erp``) and a person accepts.
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

A rate is entered as a **quote per a unit**: ``quote`` units of the group
currency per ``quoted_per`` (1, 10, 100, 1,000 or 10,000) units of the
from-currency, so its direction never flips: 0.6607 USD per 100 JPY. MariaDB
keeps 9 decimal places, and the unit keeps the digits. The TRUE rate is
quote / quoted_per. konsol computes it once, when it publishes the approved
rows to ``epm_staging.group_exchange_rates`` (``resync_staging``), and the
warehouse translates from it without scaling or inverting anything.
"""
import frappe
from frappe.model.document import Document

from konsol.period_status import assert_open

RATE_TYPES = ("Closing", "Average")
ADOPTION_SOURCE = "Adoption"
PREFILL_SOURCE = "ERP pre-fill"
MANUAL_SOURCE = "Manual"
GRAIN = ("to_currency", "from_currency", "fiscal_year", "fiscal_period", "rate_type")


class GroupExchangeRate(Document):
    #: Published by resync_staging, not a field map: the warehouse's `rate` is
    #: the true rate, computed from quote / quoted_per.
    CH_STAGING_TABLE = "epm_staging.group_exchange_rates"
    CH_STAGING_COLUMNS = ["to_currency", "from_currency", "fiscal_year", "fiscal_period", "rate_type",
                          "rate", "document"]

    @classmethod
    def resync_staging(cls, force=False):
        """Publish every approved rate as its TRUE rate: units of to per 1 from,
        Float64, the full set swapped in at once and one publish at a time
        (group_rates.publish_rates). A cancelled rate leaves. reconcile_all
        calls this too. Returns the rows published (None when it didn't)."""
        from konsol import group_rates

        return group_rates.publish_rates(force=force)

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
        self._validate_quote()
        self._validate_group_currency()
        self._track_source()
        self._set_label()
        self._require_reasons()

    def _saved_version(self):
        """The saved version of this document, or None for a new one."""
        if self.is_new():
            return None
        return self.get_doc_before_save()

    def _per(self):
        return int(self.quoted_per or 1)

    def _rate(self):
        """The true rate: units of the group currency per 1 from-currency."""
        from konsol import group_rates

        return group_rates.true_rate(self.quote, self._per())

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
        neither can be forged onto a rate a person typed."""
        before = self._saved_version()
        was = before.source if before else None
        prefilling = bool(frappe.flags.get("konsol_prefilling_rates"))
        if self.source == ADOPTION_SOURCE and was != ADOPTION_SOURCE and not self._adopting():
            frappe.throw('Source "Adoption" is set only by the one-time rate adoption (konsol#103).',
                         frappe.PermissionError)
        if self.source == PREFILL_SOURCE and was != PREFILL_SOURCE and not prefilling:
            frappe.throw('Source "ERP pre-fill" is set only by Pre-fill from ERP.', frappe.PermissionError)
        erp_before = float(before.erp_quote or 0) if before else 0.0
        if abs(float(self.erp_quote or 0) - erp_before) > 1e-12 and not (prefilling or self._adopting()):
            frappe.throw("ERP Quote is recorded by Pre-fill from ERP only.", frappe.PermissionError)

    def _validate_quote(self):
        """A positive quote per a listed unit, that keeps its digits, whose true
        rate is of plausible magnitude (#138): a fat-fingered 93.78 for 0.9378
        is refused here, not found months later."""
        from konsol import group_rates

        quote = float(self.quote or 0)
        if quote <= 0:
            frappe.throw("Quote must be a positive number.", frappe.ValidationError)
        if self._per() not in group_rates.QUOTED_PER:
            frappe.throw(f"Quoted Per must be one of {', '.join(f'{p:,}' for p in group_rates.QUOTED_PER)}.",
                         frappe.ValidationError)
        rate = self._rate()
        problem = group_rates.magnitude_problem(self.from_currency, self.to_currency, rate)
        if problem:
            frappe.throw(problem, frappe.ValidationError)
        digits = group_rates.significant_digits(quote)
        if digits < group_rates.MIN_SIGNIFICANT_DIGITS:
            better, per = group_rates.choose_quoted_per(rate)
            frappe.throw(
                f"{group_rates.quote_label(quote, self._per(), self.from_currency, self.to_currency)} keeps "
                f"only {digits} significant digits at 9 decimal places. Quote it per a larger unit: "
                f"{group_rates.quote_label(better, per, self.from_currency, self.to_currency)}.",
                frappe.ValidationError)

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
        """A pre-filled draft whose rate a person changed is a manual rate. The
        ERP Quote follows the Quoted Per, so the two stay comparable, and
        re-quoting the same rate per another unit changes nothing."""
        before = self._saved_version()
        old_per = int(before.quoted_per or 1) if before is not None else self._per()
        if old_per != self._per() and self.erp_quote:
            self.erp_quote = round(float(self.erp_quote) * self._per() / old_per, 9)
        if self.source == PREFILL_SOURCE and self.erp_quote and \
                abs(float(self.quote) - float(self.erp_quote)) > 1e-9 * max(float(self.erp_quote), 1e-9):
            self.source = MANUAL_SOURCE
        if not self.source:
            self.source = MANUAL_SOURCE

    def _set_label(self):
        from konsol import group_rates

        self.quote_label = group_rates.quote_label(self.quote, self._per(), self.from_currency, self.to_currency)

    def _require_reasons(self):
        """Say why: always for an amendment, and for a move over 50% from the
        previous approved rate for this key or from the ERP quote. Compared as
        true rates, whatever unit each is quoted per."""
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
        previous = group_rates.previous_approved(
            self.to_currency, self.from_currency, self.rate_type, self.fiscal_year, self.fiscal_period)
        erp = group_rates.true_rate(self.erp_quote, self._per()) if self.erp_quote else None
        problem = group_rates.move_problem(self._rate(), previous, erp,
                                           unit=f"{self.to_currency} per {self.from_currency}")
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
        self._publish()

    def on_cancel(self):
        self._publish()

    def _publish(self):
        """Republish the approved rates after the commit, once per transaction
        (konsol#124): a rollback publishes nothing."""
        from konsol.clickhouse import after_commit_once

        after_commit_once(("resync_staging", self.doctype), type(self).resync_staging)

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

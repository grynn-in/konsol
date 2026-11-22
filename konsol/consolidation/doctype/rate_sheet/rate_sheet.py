"""Rate Sheet: the group's governed exchange rates (konsol#592 pair 1).

Decided by Deepak Pai on 22 Nov 2026 (#592): a Statutory sheet holds the
Average and Closing quote of each currency pair for one fiscal period (D1);
a Constant sheet holds one rate per pair for a fiscal year (QA-1, QB-1). The
year and period are entered and the validity dates derived (P1-Q4 A). The
rules are in ``konsol.rate_sheet_model``; this controller reads the calendar
and the other sheets and applies them.

Ported from Group Exchange Rate, which this replaced (retired by konsol#592 T26):

* **Provenance.** A row's Source, its ERP quotes and its Legacy Documents,
  and the sheet's Copied From and Source Note, are read-only in the form
  only; REST writes any field. Each is set only under the flag of the path
  that writes it (``PREFILL_FLAG``, ``DUPLICATE_FLAG``, ``MIGRATE_FLAG``),
  and a forged one is refused with PermissionError (konsol#413, #584).
  An edit to a pre-filled, duplicated or migrated row's quote makes it Manual.
* **Reasons.** A correction or amendment says why, and so does a move over
  the Rate Move threshold (Close Settings, konsol#305-D2-9) from the previous
  approved sheet of the same basis: the previous period for Statutory, the
  previous year for Constant.
* **Magnitude warning** (konsol#179, PR #385; T26b). A quote more than the
  Magnitude Warning Factor (Close Settings) from the ISO references, but
  within the hard 10x, needs a Reason for Change, and the message names the
  Quoted Per that brings it in line. A reason covers it only if it was given
  with that quote: one carried over from a save (or an amended sheet) where
  the row's quote or Quoted Per differed does not. While the factor is
  undeclared each save says so and nothing is refused. The pre-fill,
  Duplicate Previous and the migration are not asked: no person is there.
* **Build scope** (T26b): a Constant sheet requests the scenarios build (the
  budget and forecast translation, D2); a Statutory sheet DOCTYPE_BUILD_MAP's
  "consolidation" (``build_scope``).
* **Supersede** (#312-3, #312-3b). A correction is a draft that names the
  approved sheet of its own key in ``supersedes``; that one stays approved
  and published until the correction is approved, and the approval cancels
  it in the same transaction, through its own cancel. Any other second
  draft or approved sheet for a key is refused by a locking read.
* **No plain cancel of a Statutory sheet** (#312-S3-2): only the supersede
  cancel. A Constant sheet may be cancelled while its year is not closed
  (P1-Q3 C), and is approved only then (P1-Q2 B).
* **Locks**: the fiscal year first, then the sheet rows (PR #191 re-review 2).
* **Publish after the commit**: ``resync_staging`` once per transaction, as GER.

A Constant sheet has no period. Frappe stores an unset Int as 0, so a
Constant sheet's stored period reads back 0; the controller sets it to None
on every save and never reads it into the key.
"""
import frappe
from frappe.model.document import Document

from konsol import rate_sheet_model as model
from konsol.period_status import assert_open, assert_year_not_closed
from konsol.workflows import set_cancelled_state

STATUTORY = model.STATUTORY
CONSTANT = model.CONSTANT

MANUAL = "Manual"
PREFILL = "ERP pre-fill"
DUPLICATED = "Duplicated"
MIGRATED = "Migrated"

#: Set by the path that writes each source, and by nothing else.
PREFILL_FLAG = "konsol_prefilling_rate_sheet"
DUPLICATE_FLAG = "konsol_duplicating_rate_sheet"
MIGRATE_FLAG = "konsol_migrating_rates"

ROW_FIELDS = ("name", "from_currency", "to_currency", "quoted_per", "average_quote", "closing_quote",
              "rate", "source", "erp_average_quote", "erp_closing_quote", "legacy_documents")
#: (quote field, its ERP quote field or None), per basis.
QUOTES = {
    STATUTORY: (("average_quote", "erp_average_quote"), ("closing_quote", "erp_closing_quote")),
    CONSTANT: (("rate", None),),
}
ERP_FIELDS = ("erp_average_quote", "erp_closing_quote")
QUOTE_LABELS = {"average_quote": "Average", "closing_quote": "Closing", "rate": "Rate"}
KEY_FIELDS = ("rate_basis", "fiscal_year", "fiscal_period")


def _v(obj, field):
    """A field of a Document, a child row or a namespace."""
    getter = getattr(obj, "get", None)
    if callable(getter):
        return getter(field)
    return getattr(obj, field, None)


def _num(value):
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _per(row):
    try:
        return int(_v(row, "quoted_per") or 1)
    except (TypeError, ValueError):
        return 1


def _period(basis, period):
    """The key's period: None for a Constant sheet (stored as 0), else an int."""
    if basis == CONSTANT or period in (None, ""):
        return None
    return int(period)


class RateSheet(Document):
    #: Published by resync_staging, not a field map: the warehouse's `rate` is
    #: the true rate (the table and columns Group Exchange Rate published).
    CH_STAGING_TABLE = "epm_staging.group_exchange_rates"
    CH_STAGING_COLUMNS = ["to_currency", "from_currency", "fiscal_year", "fiscal_period", "rate_type",
                          "rate", "document", "rate_set"]

    @classmethod
    def resync_staging(cls, force=False):
        """Publish every approved sheet (group_rates.publish_rates, T10).
        reconcile_all repairs the table through this."""
        from konsol import group_rates

        return group_rates.publish_rates(force=force)

    # -- naming ---------------------------------------------------------------

    def set_new_name(self, *args, **kwargs):
        """Frappe names an amendment itself (``_set_amended_name``) and skips
        autoname, so an amendment of ``RS-C-2026-1`` became ``RS-C-2026-2``, the
        name a correction of ``RS-C-2026`` takes (PR #595 review 6). Here an
        amendment is renamed through autoname: the controller's naming is the
        one source. A name given (``set_name``) or already set is kept.
        Rate Sheet ignores the site's Amended Document Naming setting:
        amendments and corrections share one -N sequence (konsol#592)."""
        force = kwargs.get("force", args[0] if args else False)
        skipped = bool(self.flags.get("name_set")) and not force
        super().set_new_name(*args, **kwargs)
        given = kwargs.get("set_name") or (args[1] if len(args) > 1 else None)
        if self.get("amended_from") and not skipped and not given:
            self.autoname()

    def autoname(self):
        """RS-S-<FY>-<PP> or RS-C-<FY>; a correction or an amendment takes the
        next free -2, -3, ... (``set_new_name`` routes an amendment here)."""
        base = self._base_name()
        name, n = base, 1
        while frappe.db.exists(self.doctype, name):
            n += 1
            name = f"{base}-{n}"
        self.name = name

    def _base_name(self):
        try:
            year = int(self.fiscal_year)
        except (TypeError, ValueError):
            frappe.throw("A Rate Sheet needs a Fiscal Year.", frappe.MandatoryError)
        if self.rate_basis == CONSTANT:
            return f"RS-C-{year}"
        if self.rate_basis != STATUTORY:
            frappe.throw(f"Rate Basis must be Statutory or Constant, not {self.rate_basis!r}.",
                         frappe.ValidationError)
        if self.fiscal_period in (None, ""):
            frappe.throw("A Statutory sheet needs a Fiscal Period.", frappe.MandatoryError)
        return f"RS-S-{year}-{int(self.fiscal_period):02d}"

    # -- validate -------------------------------------------------------------

    def validate(self):
        self._normalise_key()
        self._set_window()
        self._guard_provenance()
        self._validate_rows()
        self._validate_group_currencies()
        self._track_sources()
        self._set_labels()
        self._validate_supersedes()
        self._refuse_second_sheet()
        self._require_reasons()
        self._check_magnitude_warning()

    def _normalise_key(self):
        # A Constant sheet has no period. It is stored as 0, what the Int
        # column reads back: storing None made every submit's diff hold
        # ('fiscal_period', 0, None), so the approver counted as a preparer
        # (self_approval._edits_in_this_submit, PR #595 live test). Readers
        # take the period through _period() or _as_sheet(), None for Constant.
        if self.rate_basis == CONSTANT and self.fiscal_period in (None, "", 0, "0"):
            self.fiscal_period = 0
        try:
            year = int(self.fiscal_year)
        except (TypeError, ValueError):
            frappe.throw("A Rate Sheet needs a Fiscal Year.", frappe.MandatoryError)
        # ClickHouse Date holds 1970-01-01..2149-06-06 and clamps silently.
        if not 1970 <= year <= 2148:
            frappe.throw(f"Fiscal Year {self.fiscal_year} is out of range.", frappe.ValidationError)

    def _key(self):
        return (self.rate_basis, int(self.fiscal_year), _period(self.rate_basis, self.fiscal_period))

    def _key_label(self):
        basis, year, period = self._key()
        return f"{basis} sheet for {model.key_label(basis, year, period)}"

    def _rows(self):
        return list(self.get("rates") or [])

    def _as_sheet(self):
        """This sheet in the shape rate_sheet_model reads."""
        return {"rate_basis": self.rate_basis, "fiscal_year": self.fiscal_year,
                # The stored 0 of a Constant sheet is the model's None; any
                # other period is passed on, for the model to refuse.
                "fiscal_period": (None if self.rate_basis == CONSTANT and self.fiscal_period in (0, "0")
                                  else self.fiscal_period),
                "name": self.name, "docstatus": self.docstatus,
                "rows": [{f: _v(r, f) for f in ROW_FIELDS} for r in self._rows()]}

    def _calendar(self):
        """The declared periods of this sheet's year, as rate_sheet_model.window reads them."""
        return frappe.db.sql(
            "SELECT y.fiscal_year AS fiscal_year, p.fiscal_period AS period, p.period_type AS type, "
            "p.start_date AS start_date, p.end_date AS end_date, p.period_code AS period_code, "
            "p.period_label AS period_label "
            "FROM `tabEPM Fiscal Year` y JOIN `tabEPM Fiscal Year Period` p "
            "ON p.parent = y.name AND p.parenttype = 'EPM Fiscal Year' AND p.parentfield = 'periods' "
            "WHERE y.fiscal_year = %s ORDER BY p.fiscal_period",
            (int(self.fiscal_year),), as_dict=True)

    def _set_window(self):
        calendar = self._calendar()
        dates, why = model.window(self._as_sheet(), calendar)
        if why:
            frappe.throw(why, frappe.ValidationError)
        self.valid_from, self.valid_to = dates
        basis, year, period = self._key()
        if basis == CONSTANT:
            self.period_label = f"FY{year}"
        else:
            row = next(r for r in calendar if int(r["period"]) == period)
            self.period_label = row.get("period_label") or row.get("period_code") or f"P{period}"
        self.title = f"{basis} {model.key_label(basis, year, period)}"

    def _saved_version(self):
        if self.is_new():
            return None
        return self.get_doc_before_save()

    def _saved_rows(self):
        before = self._saved_version()
        rows = (_v(before, "rates") or []) if before is not None else []
        return {_v(r, "name"): r for r in rows if _v(r, "name")}

    # -- provenance -----------------------------------------------------------

    def _flag(self, name):
        return bool(frappe.flags.get(name))

    def _migrating(self):
        """The one-time migration from Group Exchange Rate (T25): only the
        system, only inside the run. It records what a period was already
        translated at, so it may approve into a closed period."""
        return self._flag(MIGRATE_FLAG) and frappe.session.user == "Administrator"

    def _guard_provenance(self):
        """Source, the ERP quotes, Legacy Documents, Copied From and Source
        Note are set only under the flag of the path that writes them (GER
        _guard_provenance, konsol#413, #584)."""
        prefilling, duplicating, migrating = (self._flag(PREFILL_FLAG), self._flag(DUPLICATE_FLAG),
                                              self._migrating())
        writers = {PREFILL: (prefilling, "Pre-fill from ERP"),
                   DUPLICATED: (duplicating, "Duplicate Previous"),
                   MIGRATED: (migrating, "the migration from Group Exchange Rate")}
        saved = self._saved_rows()
        for row in self._rows():
            before = saved.get(_v(row, "name"))
            label = f"{_v(row, 'from_currency')} → {_v(row, 'to_currency')}"
            source, was = _v(row, "source"), (_v(before, "source") if before is not None else None)
            if source in writers and was != source and not writers[source][0]:
                frappe.throw(f'{label}: Source "{source}" is set only by {writers[source][1]}.',
                             frappe.PermissionError)
            for field in ERP_FIELDS:
                old = _num(_v(before, field)) if before is not None else 0.0
                if abs(_num(_v(row, field)) - old) > 1e-12 and not (prefilling or migrating):
                    frappe.throw(f"{label}: the ERP quotes are recorded by Pre-fill from ERP only.",
                                 frappe.PermissionError)
            old_legacy = (_v(before, "legacy_documents") or "") if before is not None else ""
            if (_v(row, "legacy_documents") or "") != old_legacy and not migrating:
                frappe.throw(f"{label}: Legacy Documents are recorded by the migration only.",
                             frappe.PermissionError)
        before = self._saved_version()
        if (self.get("copied_from") or "") != ((_v(before, "copied_from") or "") if before else "") \
                and not duplicating:
            frappe.throw("Copied From is recorded by Duplicate Previous only.", frappe.PermissionError)
        if (self.get("source_note") or "") != ((_v(before, "source_note") or "") if before else "") \
                and not (prefilling or duplicating or migrating):
            frappe.throw("Source Note is recorded by Pre-fill from ERP, Duplicate Previous or the "
                         "migration only.", frappe.PermissionError)

    # -- rows -------------------------------------------------------------------

    def _validate_rows(self):
        """rate_sheet_model.row_problems, then GER's quote checks on each rate:
        a magnitude more than 10x from the ISO references is a scaling error
        (#138), and a quote must keep its digits at 9 decimal places."""
        from konsol import group_rates

        problems = model.row_problems(self._as_sheet())
        if problems:
            frappe.throw("<br>".join(problems), frappe.ValidationError)
        rows = self._rows()
        refs = group_rates.usd_references(
            [c for r in rows for c in (_v(r, "from_currency"), _v(r, "to_currency"))])
        for row in rows:
            for field, _ in QUOTES[self.rate_basis]:
                quote, per = _num(_v(row, field)), _per(row)
                rate = model.true_rate(quote, per)
                problem = group_rates.magnitude_problem(_v(row, "from_currency"), _v(row, "to_currency"),
                                                        rate, refs)
                if problem:
                    problems.append(problem)
                digits = group_rates.significant_digits(quote)
                if digits < group_rates.MIN_SIGNIFICANT_DIGITS:
                    problems.append(
                        f"{group_rates.quote_label(quote, per, _v(row, 'from_currency'), _v(row, 'to_currency'))}"
                        f" keeps only {digits} significant digits at 9 decimal places: quote it per a "
                        "larger unit (Quoted Per).")
        if problems:
            frappe.throw("<br>".join(problems), frappe.ValidationError)

    def _validate_group_currencies(self):
        """Only rates INTO a group reporting currency are entered (GER
        _validate_group_currency); cross rates are derived."""
        for currency in sorted({_v(r, "to_currency") for r in self._rows() if _v(r, "to_currency")}):
            if not frappe.db.sql(
                "SELECT 1 FROM `tabConsolidation Group` WHERE reporting_currency = %s "
                "AND (is_group = 1 OR IFNULL(data_area_id, '') = '') LIMIT 1",
                (currency,),
            ):
                frappe.throw(
                    f"{currency} is not the reporting currency of any consolidation group. "
                    "Enter rates into a group's reporting currency only; cross rates are derived.",
                    frappe.ValidationError)

    def _track_sources(self):
        """A proposed row (pre-filled, duplicated, migrated) whose rate a
        person changed is a Manual row (GER _track_source). The ERP quotes
        follow Quoted Per, so re-quoting the same rate per another unit
        changes nothing."""
        saved = self._saved_rows()
        for row in self._rows():
            before = saved.get(_v(row, "name"))
            source = _v(row, "source")
            per = _per(row)
            if before is not None:
                old_per = _per(before)
                if old_per != per:
                    for field in ERP_FIELDS:
                        if _v(row, field):
                            setattr(row, field, round(_num(_v(row, field)) * per / old_per, 9))
            if source == PREFILL:
                for field, erp in QUOTES[self.rate_basis]:
                    if erp and _v(row, erp):
                        quote, ref = _num(_v(row, field)), _num(_v(row, erp))
                        if abs(quote - ref) > 1e-9 * max(ref, 1e-9):
                            source = MANUAL
            if source in (PREFILL, DUPLICATED, MIGRATED) and before is not None:
                if any(str(_v(row, f)) != str(_v(before, f)) for f in ("from_currency", "to_currency")):
                    source = MANUAL
                for field, _ in QUOTES[self.rate_basis]:
                    now = model.true_rate(_v(row, field), per)
                    was = model.true_rate(_v(before, field), _per(before))
                    if abs(now - was) > 1e-9 * max(abs(was), 1e-12):
                        source = MANUAL
            row.source = source or MANUAL

    def _set_labels(self):
        from konsol import group_rates

        for row in self._rows():
            f, t, per = _v(row, "from_currency"), _v(row, "to_currency"), _per(row)
            if self.rate_basis == CONSTANT:
                row.quote_label = group_rates.quote_label(_v(row, "rate"), per, f, t)
            else:
                row.quote_label = (f"Average {group_rates.quote_label(_v(row, 'average_quote'), per, f, t)}; "
                                   f"Closing {group_rates.quote_label(_v(row, 'closing_quote'), per, f, t)}")

    # -- supersede and the one-sheet-per-key rule -----------------------------------------

    def _validate_supersedes(self):
        """A correction names the approved sheet of its own key (#312-3).
        Read without a lock here; the locking reads below are the authority."""
        name = self.get("supersedes")
        if not name:
            return
        other = None if name == self.name else frappe.db.get_value(
            self.doctype, name, list(KEY_FIELDS) + ["docstatus"], as_dict=True)
        same_key = other is not None and int(other.docstatus or 0) == 1 and (
            other.rate_basis, int(other.fiscal_year), _period(other.rate_basis, other.fiscal_period)
        ) == self._key()
        if not same_key:
            frappe.throw(f"{name} cannot be superseded by this sheet: a correction supersedes the "
                         f"approved {self._key_label()}.", frappe.ValidationError)

    def _lock_year(self):
        """The fiscal year row, LOCK IN SHARE MODE: every path takes the year
        before any sheet row, as a period close does (PR #191 re-review 2).
        ``status`` is selected so the lock lands on the row, not only on the
        fiscal_year index entry."""
        frappe.db.sql("SELECT name, status FROM `tabEPM Fiscal Year` WHERE fiscal_year=%s LOCK IN SHARE MODE",
                      (int(self.fiscal_year),))

    def _key_where(self):
        basis, year, period = self._key()
        if basis == CONSTANT:
            return "rate_basis = %s AND fiscal_year = %s", (basis, year)
        return "rate_basis = %s AND fiscal_year = %s AND fiscal_period = %s", (basis, year, period)

    def _refuse_second_sheet(self):
        """One draft or approved sheet per key, besides the one a correction
        supersedes. A locking read (REPEATABLE READ: a plain read can miss a
        sheet committed since this transaction's first read)."""
        self._lock_year()
        where, params = self._key_where()
        rows = frappe.db.sql(
            f"SELECT name, docstatus FROM `tabRate Sheet` WHERE {where} AND docstatus < 2 "
            "AND name != %s FOR UPDATE",
            params + (self.name,))
        supersedes = self.get("supersedes")
        others = [(name, int(ds or 0)) for name, ds in rows if name != supersedes]
        if others:
            name, docstatus = others[0]
            what = "approved" if docstatus == 1 else "draft"
            frappe.throw(
                f"{name} is already the {what} {self._key_label()}. To correct an approved sheet, "
                "supersede it (a new draft that names it in Supersedes, with a Reason for Change); "
                "edit or delete a draft instead of adding another.",
                frappe.DuplicateEntryError)

    # -- reasons ----------------------------------------------------------------------------

    def _require_reasons(self):
        """Say why: always for a correction or an amendment, and for a move
        over the declared Rate Move threshold from the previous approved
        sheet of the same basis or from a row's ERP quote (GER
        _require_reasons). Compared as true rates."""
        from konsol import group_rates

        if (self.change_reason or "").strip():
            return
        if self.get("supersedes"):
            frappe.throw(f"Say why this sheet replaces {self.supersedes} (Reason for Change).",
                         frappe.MandatoryError)
        if self.amended_from:
            frappe.throw("Say why this sheet replaces the one it amends (Reason for Change). The "
                         "cancelled sheet and this reason are the audit trail.", frappe.MandatoryError)
        if self._migrating():
            return  # the rates a period was already translated at
        # PR #595 review 5 (GER's #586 review): a duplicate's rate equal to the
        # rate it copies is a move of 0 and needs no reason, even while the
        # threshold is undeclared. Decided from the stored, guarded
        # copied_from, so a later save or Submit keeps it (no insert-time
        # flag), and only for rows still Duplicated: an edited row is Manual
        # (_track_sources) and is judged against the previous approved sheet.
        sheet = self._as_sheet()
        copy = self._copied_sheet()
        rows = sheet["rows"]
        if copy is not None:
            kept = [r for r in rows if r.get("source") == DUPLICATED]
            others = [r for r in rows if r.get("source") != DUPLICATED]
            problems = model.move_problems(dict(sheet, rows=kept), copy, group_rates.move_threshold(),
                                           copied=True)
            if others:
                problems += model.move_problems(dict(sheet, rows=others), self._previous_approved(),
                                                group_rates.move_threshold())
        else:
            problems = model.move_problems(sheet, self._previous_approved(), group_rates.move_threshold())
        if problems:
            frappe.throw("<br>".join(dict.fromkeys(problems)), frappe.MandatoryError)

    def _check_magnitude_warning(self):
        """konsol#179, per row and per quote (GER _check_magnitude_warning)."""
        from konsol import group_rates

        if self._flag(PREFILL_FLAG) or self._flag(DUPLICATE_FLAG) or self._migrating():
            return
        factor = group_rates.magnitude_warning_factor()
        if factor is None:
            frappe.msgprint(group_rates.MAGNITUDE_WARNING_UNDECLARED, title="Magnitude Warning Factor",
                            indicator="orange")
            return
        rows = self._rows()
        refs = group_rates.usd_references(
            [c for r in rows for c in (_v(r, "from_currency"), _v(r, "to_currency"))])
        warned = []   # (row, quote field, message)
        for row in rows:
            for field, _ in QUOTES[self.rate_basis]:
                problem = group_rates.magnitude_warning(_v(row, "from_currency"), _v(row, "to_currency"),
                                                        _num(_v(row, field)), _per(row), factor, refs)
                if problem:
                    label = "" if self.rate_basis == CONSTANT else f"{QUOTE_LABELS[field]}: "
                    warned.append((row, field, label + problem))
        if not warned:
            return
        reason = (self.change_reason or "").strip()
        if not reason:
            frappe.throw("<br>".join(msg for _, _, msg in warned), frappe.MandatoryError)
        stale = self._reason_predates(reason, warned)
        if stale:
            frappe.throw("<br>".join(stale) + "<br>The Reason for Change was given before Quote or Quoted "
                         "Per changed: update it to say why this quote is right.", frappe.MandatoryError)

    def _reason_predates(self, reason, warned):
        """PR #385 review: a Reason for Change covers a warned quote only if it
        was given with that quote. The prior version is the saved sheet, or
        for an amendment the sheet it amends. If that version carried this
        same reason, every warned quote whose row (matched by currency pair)
        had another quote or Quoted Per there, or was not there, is stale.
        Returns the stale messages."""
        prior_reason, prior_rows = self._prior_reason_and_rows()
        if prior_reason is None or prior_reason.strip() != reason:
            return []
        stale = []
        for row, field, msg in warned:
            before = prior_rows.get((_v(row, "from_currency"), _v(row, "to_currency")))
            if before is None or (_num(_v(before, field)), _per(before)) != (_num(_v(row, field)), _per(row)):
                stale.append(msg)
        return stale

    def _prior_reason_and_rows(self):
        """(change_reason, {(from, to): row}) of the saved version, else of
        the amended sheet; (None, {}) for a new sheet that amends nothing."""
        before = self._saved_version()
        if before is not None:
            rows = _v(before, "rates") or []
            return (_v(before, "change_reason") or ""), {(_v(r, "from_currency"), _v(r, "to_currency")): r
                                                         for r in rows}
        if self.amended_from:
            meta = frappe.db.get_value(self.doctype, self.amended_from, ["change_reason"], as_dict=True)
            if meta is None:
                return None, {}
            sheet = self._load_sheet(self.amended_from)
            return (meta.change_reason or ""), {(r["from_currency"], r["to_currency"]): r
                                                for r in sheet["rows"]}
        return None, {}

    # -- build scope ----------------------------------------------------------------------------

    def build_scope(self):
        """The build this sheet requests (tasks.queue_consolidation_build): a
        Constant sheet drives the budget and forecast translation (D2), which
        the scenarios domain builds; None for a Statutory sheet, whose actuals
        translation is DOCTYPE_BUILD_MAP's "consolidation"."""
        return "scenarios" if self.rate_basis == CONSTANT else None

    def _previous_approved(self):
        """The latest approved sheet of the same basis before this key: the
        previous period for Statutory (the one before a gap, if any), the
        previous year for Constant. None when there is none."""
        basis, year, period = self._key()
        if basis == CONSTANT:
            rows = frappe.db.sql(
                "SELECT name FROM `tabRate Sheet` WHERE rate_basis = %s AND docstatus = 1 "
                "AND fiscal_year < %s AND name != %s ORDER BY fiscal_year DESC LIMIT 1",
                (basis, year, self.name))
        else:
            rows = frappe.db.sql(
                "SELECT name FROM `tabRate Sheet` WHERE rate_basis = %s AND docstatus = 1 "
                "AND (fiscal_year < %s OR (fiscal_year = %s AND fiscal_period < %s)) AND name != %s "
                "ORDER BY fiscal_year DESC, fiscal_period DESC LIMIT 1",
                (basis, year, year, period, self.name))
        return self._load_sheet(rows[0][0]) if rows else None

    def _copied_sheet(self):
        """The sheet ``copied_from`` names, which a Duplicated row's move is
        judged against (GER #586 review), if it is an approved sheet of the
        same basis. A copied sheet a correction has since cancelled is
        replaced by the sheet that superseded it (the approved one)."""
        name = self.get("copied_from")
        for _ in range(20):  # a chain of corrections; bounded against a cycle
            meta = frappe.db.get_value(self.doctype, name, list(KEY_FIELDS) + ["docstatus"],
                                       as_dict=True) if name else None
            if meta is None or meta.rate_basis != self.rate_basis:
                return None
            if int(meta.docstatus or 0) == 1:
                return self._load_sheet(name, meta)
            if int(meta.docstatus or 0) != 2:
                return None
            nxt = frappe.db.sql("SELECT name FROM `tabRate Sheet` WHERE supersedes = %s AND docstatus = 1 "
                                "LIMIT 1", (name,))
            name = nxt[0][0] if nxt else None
        return None

    def _load_sheet(self, name, meta=None):
        meta = meta or frappe.db.get_value(self.doctype, name, list(KEY_FIELDS) + ["docstatus"], as_dict=True)
        rows = frappe.get_all("Rate Sheet Row",
                              filters={"parent": name, "parenttype": "Rate Sheet", "parentfield": "rates"},
                              fields=[f for f in ROW_FIELDS if f != "name"], order_by="idx asc")
        return {"name": name, "rate_basis": meta.rate_basis, "fiscal_year": meta.fiscal_year,
                "fiscal_period": _period(meta.rate_basis, meta.fiscal_period), "docstatus": meta.docstatus,
                "rows": [{f: _v(r, f) for f in ROW_FIELDS if f != "name"} for r in rows]}

    # -- lifecycle ----------------------------------------------------------------------------

    def check_if_latest(self):
        """Lock the fiscal year before Frappe's own check_if_latest locks this
        sheet's row (GER check_if_latest, PR #191 re-review 2 finding 1). A
        bare lock, so an undeclared year still reaches validate's own
        message; only for a saved sheet, as Frappe's own lock."""
        if not self.is_new():
            try:
                year = int(self.fiscal_year)
            except (TypeError, ValueError):
                year = None
            if year is not None:
                frappe.db.sql(
                    "SELECT name, status FROM `tabEPM Fiscal Year` WHERE fiscal_year=%s LOCK IN SHARE MODE",
                    (year,))
        super().check_if_latest()

    def before_submit(self):
        """Submit is the approval. A Statutory sheet's period must be open; a
        Constant sheet's year must not be closed (P1-Q2 B). The migration
        records what closed periods were translated at, so it is exempt."""
        if not self._migrating():
            if self.rate_basis == CONSTANT:
                assert_year_not_closed(self.fiscal_year, action="approve a Constant rate sheet")
            else:
                assert_open(self.fiscal_year, self.fiscal_period, action="approve a rate sheet")
        self._refuse_second_approved_sheet()

    def _refuse_second_approved_sheet(self):
        """One approved sheet per key (GER _refuse_second_approved_rate). The
        sheet a correction supersedes is exempt, and only that one: on_submit
        cancels it in this transaction. It must still be approved now."""
        where, params = self._key_where()
        approved = [row[0] for row in frappe.db.sql(
            f"SELECT name FROM `tabRate Sheet` WHERE {where} AND docstatus = 1 AND name != %s FOR UPDATE",
            params + (self.name,))]
        supersedes = self.get("supersedes")
        others = [name for name in approved if name != supersedes]
        if others:
            frappe.throw(f"{others[0]} is already the approved {self._key_label()}. Correct it by "
                         "superseding it instead.", frappe.DuplicateEntryError)
        if supersedes and supersedes not in approved:
            frappe.throw(f"{supersedes} is no longer approved, so this correction has nothing to "
                         f"replace. Delete it and draft a new {self._key_label()}.",
                         frappe.ValidationError)

    def on_submit(self):
        self._cancel_superseded()
        self._publish()

    def _cancel_superseded(self):
        """Approval of a correction cancels the sheet it replaces, in this
        transaction and through its own cancel: its gates run, and the
        on_cancel hooks write the trail's approval_cancelled event with
        ``superseded_by`` (close/cancel_event.py) and republish. This sheet
        links to it through ``supersedes``, so Frappe's back-link check would
        refuse the cancel; only Rate Sheet links are ignored, for this cancel."""
        name = self.get("supersedes")
        if not name:
            return
        superseded = frappe.get_doc(self.doctype, name, for_update=True)
        superseded.flags.superseded_by = self.name
        superseded.ignore_linked_doctypes = [self.doctype]
        superseded.cancel()

    def before_cancel(self):
        """No plain cancel of a Statutory sheet (#312-S3-2): the only cancel
        is the one a correction's approval makes (``flags.superseded_by``, set
        by _cancel_superseded alone; ``flags`` cannot come from a request).
        The refusal comes before the period gate: a reopen would not change
        it. A Constant sheet may be cancelled while its year is not closed
        (P1-Q3 C). The migration builds cancelled predecessor sheets of closed
        periods, so it is exempt."""
        migrating = self._migrating()
        if self.rate_basis != CONSTANT and not self.flags.get("superseded_by") and not migrating:
            frappe.throw(f"{self.name} is the approved {self._key_label()}: an approved Statutory sheet "
                         "cannot be cancelled. Correct it by superseding it (a new draft that names it, "
                         "with a Reason for Change).", frappe.ValidationError)
        if not migrating:
            if self.rate_basis == CONSTANT:
                assert_year_not_closed(self.fiscal_year, action="cancel a Constant rate sheet")
            else:
                assert_open(self.fiscal_year, self.fiscal_period, action="cancel a rate sheet")
        set_cancelled_state(self)

    def on_cancel(self):
        self._publish()

    def _publish(self):
        """Republish the approved rates after the commit, once per transaction
        (konsol#124): a rollback publishes nothing."""
        from konsol.clickhouse import after_commit_once

        after_commit_once(("resync_staging", "Rate Sheet"), type(self).resync_staging)

    def on_trash(self):
        """A cancelled sheet is the record of what was once approved; it is
        kept. A draft may be deleted (P1-Q3 A)."""
        if self.docstatus == 2:
            frappe.throw("A cancelled rate sheet is kept as the audit trail.", frappe.PermissionError)


def on_doctype_update():
    """The key's index, for the one-sheet-per-key locking reads."""
    frappe.db.add_index("Rate Sheet", list(KEY_FIELDS), index_name="sheet_key")

import frappe
from frappe.model.document import Document
from frappe.utils import cint, get_datetime, getdate

from konsol import fiscal_calendar
from konsol import fiscal_patterns_model as fpm
from konsol import fiscal_status_model as fstm
from konsol import fiscal_structure_model as fsm

#: Labels of the fields only the Close/Lock/Reopen actions (or the migration
#: patch) set, in the order _status_values() returns them.
_STATUS_LABELS = ("Status", "Closed By", "Closed On")

_ACTIONS_NOTE = "is set by Close Year, Lock Year and Reopen Year, not by editing."

#: Roles that may run Generate Periods (EPM Admin is the Close Lead).
_GENERATE_ROLES = frozenset({"EPM Admin", "System Manager"})


def _int(value):
    """An Int field as an int; blank stays None and a non-number stays as
    given, so the checks name it rather than reading it as 0."""
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return value


def _date(value):
    """A Date field as a date; blank stays None (getdate(None) is today)."""
    return getdate(value) if value else None


def _status(value):
    """A Select status; blank reads as the field default, Open."""
    return value or fstm.OPEN


def _status_values(doc):
    """(status, closed_by, closed_on) normalised for comparison; `doc` None
    stands for a not-yet-saved record: Open, never closed."""
    if doc is None:
        return (fstm.OPEN, None, None)
    closed_on = getattr(doc, "closed_on", None)
    return (
        _status(getattr(doc, "status", None)),
        getattr(doc, "closed_by", None) or None,
        get_datetime(closed_on) if closed_on else None,
    )


def _year_dict(doc):
    """The year fields as fiscal_structure_model reads them."""
    return {
        "year": _int(doc.fiscal_year),
        "start_date": _date(doc.start_date),
        "end_date": _date(doc.end_date),
    }


def _row_dicts(doc):
    """The period rows as fiscal_structure_model reads them."""
    return [
        {
            "period": _int(r.fiscal_period),
            "code": r.period_code,
            "type": r.period_type,
            "start_date": _date(r.start_date),
            "end_date": _date(r.end_date),
            "status": _status(r.status),
        }
        for r in (doc.periods or [])
    ]


def _stamp(target, new, now):
    """Set the year's or a row's status to `new`: closed_by/closed_on name the
    user and time, or are cleared when `new` is Open."""
    target.status = new
    if new == fstm.OPEN:
        target.closed_by = None
        target.closed_on = None
    else:
        target.closed_by = frappe.session.user
        target.closed_on = now


def _rate_failure(fiscal_year, fiscal_period):
    """The group-rate gate's refusal for one period, or None if it passes.
    frappe.throw logs its message before raising; the caught refusal's message
    is taken back off the log, since the caller throws one message naming
    every failure instead."""
    from konsol import group_rates
    log = getattr(getattr(frappe, "local", None), "message_log", None)
    logged = len(log) if log is not None else 0
    try:
        group_rates.assert_rates_complete(fiscal_year, fiscal_period)
    except frappe.ValidationError as e:
        if log is not None:
            del log[logged:]
        return str(e)
    return None


class EPMFiscalYear(Document):
    def validate(self):
        year = _year_dict(self)
        rows = _row_dicts(self)

        errors = (
            fsm.period_problems(rows)
            + fsm.year_problems(year, rows)
            + fsm.regular_period_problems(year, rows)
            + fsm.placement_problems(year, rows)
        )

        before = self.get_doc_before_save()
        self._check_status_fields_unchanged(before)

        previous_codes = (
            None if before is None else {r.period_code for r in (before.periods or [])}
        )
        errors += fstm.row_problems(_status(self.status), rows, previous_codes)

        # Periods documents already use are frozen. A new year has no saved
        # version to compare with, and the migration patch creates years from
        # data that already uses them.
        if before is not None and not self.flags.konsol_fiscal_migration:
            errors += fsm.used_period_problems(
                _year_dict(before), year, _row_dicts(before), rows,
                fiscal_calendar.periods_in_use(self.fiscal_year))
        if errors:
            frappe.throw("\n".join(errors))

    def on_trash(self):
        """Refuse deleting a year whose periods documents use. on_trash runs
        before the delete (after_delete would be too late to refuse)."""
        used = fiscal_calendar.periods_in_use(self.fiscal_year)
        if used:
            frappe.throw(
                f"FY{self.fiscal_year} can't be deleted: "
                f"documents use {len(used)} of its periods."
            )

    @frappe.whitelist(methods=["POST"])
    def generate_periods(self):
        """Replace the period table with the rows the year's pattern gives,
        then save (validate runs as usual). Only EPM Admin or System Manager,
        and only on an Open year none of whose periods documents use."""
        if not _GENERATE_ROLES.intersection(frappe.get_roles()):
            frappe.throw("Only an EPM Admin can generate periods.", frappe.PermissionError)

        status = _status(self.status)
        if status != fstm.OPEN:
            frappe.throw(f"FY{self.fiscal_year} is {status}; periods can't be generated.")

        used = fiscal_calendar.periods_in_use(self.fiscal_year)
        if used:
            frappe.throw(
                f"FY{self.fiscal_year}: documents use {len(used)} of its periods; "
                "generate only on an unused year."
            )

        try:
            rows = fpm.generate_periods(
                self.period_pattern, _date(self.start_date), _date(self.end_date),
                bool(cint(self.include_opening_period)),
                bool(cint(self.include_closing_period)))
        except ValueError as e:
            frappe.throw(str(e))

        self.set("periods", [])
        for r in rows:
            self.append("periods", {
                "fiscal_period": r["period"],
                "period_code": r["code"],
                "period_label": r["label"],
                "period_type": r["type"],
                "start_date": r["start_date"],
                "end_date": r["end_date"],
                "quarter": r["quarter"],
                "status": fstm.OPEN,
            })
        self.save()

    @frappe.whitelist(methods=["POST"])
    def close_period(self, fiscal_period, note=None):
        """Close one period row; leaving Open checks its group rates first."""
        return self._set_period_status(fiscal_period, fstm.CLOSED, "closed", note)

    @frappe.whitelist(methods=["POST"])
    def lock_period(self, fiscal_period, note=None):
        """Lock one period row; leaving Open checks its group rates first."""
        return self._set_period_status(fiscal_period, fstm.LOCKED, "locked", note)

    @frappe.whitelist(methods=["POST"])
    def reopen_period(self, fiscal_period, reason):
        """Reopen one period row of an Open year, for a stated reason."""
        return self._set_period_status(fiscal_period, fstm.OPEN, "reopened", reason)

    def _set_period_status(self, fiscal_period, new, verb, text):
        """Move the row numbered `fiscal_period` to `new`, as
        fiscal_status_model.transition_problem allows for the user's roles,
        then save as a status action (validate still runs). A row leaving Open
        must pass the group-rate gate before anything changes. Reopening
        needs a reason and an Open year. `text` (a note, or the reason) goes
        onto the year's closing note with the period code and date."""
        wanted = _int(fiscal_period)
        row = next((r for r in (self.periods or []) if _int(r.fiscal_period) == wanted), None)
        if row is None:
            frappe.throw(f"FY{self.fiscal_year} has no fiscal period {fiscal_period}.")

        current = _status(row.status)
        if current == new:
            frappe.throw(f"Period {row.period_code} is already {new}.")

        problem = fstm.transition_problem(current, new, frappe.get_roles())
        if problem:
            frappe.throw(f"Period {row.period_code}: {problem}", frappe.PermissionError)

        text = (text or "").strip()
        if new == fstm.OPEN:
            if not text:
                frappe.throw(f"Give a reason for reopening period {row.period_code}.")
            year_status = _status(self.status)
            if year_status != fstm.OPEN:
                frappe.throw(
                    f"FY{self.fiscal_year} is {year_status}; Reopen the year first, "
                    f"then period {row.period_code}.")
        elif current == fstm.OPEN:
            from konsol import group_rates
            group_rates.assert_rates_complete(self.fiscal_year, row.fiscal_period)

        now = frappe.utils.now_datetime()
        _stamp(row, new, now)
        self._append_note(row.period_code, verb, text, now)

        self.flags.konsol_status_action = True
        self.save()
        return {"fiscal_period": row.fiscal_period, "period_code": row.period_code, "status": new}

    @frappe.whitelist(methods=["POST"])
    def close_year(self, note=None):
        """Close the year and every Open row; all or nothing on group rates."""
        return self._set_year_status(fstm.CLOSED, "closed", note)

    @frappe.whitelist(methods=["POST"])
    def lock_year(self, note=None):
        """Lock the year and every Open or Closed row; all or nothing on
        group rates (only the rows leaving Open are checked)."""
        return self._set_year_status(fstm.LOCKED, "locked", note)

    @frappe.whitelist(methods=["POST"])
    def reopen_year(self, reason):
        """Reopen the year, for a stated reason; its rows stay as they are
        (each period is reopened on its own)."""
        return self._set_year_status(fstm.OPEN, "reopened", reason)

    def _set_year_status(self, new, verb, text):
        """Move the year to `new`, as fiscal_status_model.transition_problem
        allows for the user's roles. Closing or locking also moves every row
        looser than `new` to it; every row leaving Open is rate-checked first
        and, if any fail, one message names them all and nothing changes.
        Reopening needs a reason and leaves the rows alone. `text` goes onto
        the closing note with the year and date."""
        label = f"FY{self.fiscal_year}"
        current = _status(self.status)
        if current == new:
            frappe.throw(f"{label} is already {new}.")

        problem = fstm.transition_problem(current, new, frappe.get_roles())
        if problem:
            frappe.throw(f"{label}: {problem}", frappe.PermissionError)

        text = (text or "").strip()
        moving = []
        if new == fstm.OPEN:
            if not text:
                frappe.throw(f"Give a reason for reopening {label}.")
        else:
            moving = [r for r in (self.periods or [])
                      if fstm.effective_status(new, _status(r.status)) != _status(r.status)]
            failures = []
            for r in moving:
                if _status(r.status) == fstm.OPEN:
                    failure = _rate_failure(self.fiscal_year, r.fiscal_period)
                    if failure:
                        failures.append(f"{r.period_code}: {failure}")
            if failures:
                frappe.throw(
                    f"{label} can't be {verb}: {len(failures)} of its periods fail the "
                    "group-rate check; nothing was changed.\n" + "\n".join(failures))

        now = frappe.utils.now_datetime()
        for r in moving:
            _stamp(r, new, now)
        _stamp(self, new, now)
        self._append_note(label, verb, text, now)

        self.flags.konsol_status_action = True
        self.save()
        return {"fiscal_year": self.fiscal_year, "status": new,
                "periods_moved": [r.period_code for r in moving]}

    def _append_note(self, subject, verb, text, now):
        """Add "<subject> <verb> on <date> by <user>: <text>" to the closing
        note; nothing when `text` is blank."""
        if text:
            line = f"{subject} {verb} on {getdate(now)} by {frappe.session.user}: {text}"
            self.closing_note = f"{self.closing_note}\n{line}" if self.closing_note else line

    def _check_status_fields_unchanged(self, before):
        """Refuse, as a PermissionError, any change to the status fields of
        the year or a row unless an action or the migration patch is saving.
        A row is matched to its saved version by period code; a row with no
        saved version (and a new year) is compared with Open, never closed."""
        if self.flags.konsol_status_action or self.flags.konsol_fiscal_migration:
            return

        problems = []
        for label, new, old in zip(_STATUS_LABELS, _status_values(self), _status_values(before)):
            if new != old:
                problems.append(f"{label} {_ACTIONS_NOTE}")

        saved_rows = {r.period_code: r for r in ((before.periods or []) if before else [])}
        for r in self.periods or []:
            old_values = _status_values(saved_rows.get(r.period_code))
            for label, new, old in zip(_STATUS_LABELS, _status_values(r), old_values):
                if new != old:
                    problems.append(f"Period {r.period_code}: {label} {_ACTIONS_NOTE}")

        if problems:
            frappe.throw("\n".join(problems), frappe.PermissionError)

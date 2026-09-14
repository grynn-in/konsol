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

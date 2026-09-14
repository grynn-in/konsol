"""Period close state — the one place that answers "is this period open?".

konsol#189: periods are declared, never assumed. A period exists only as a
row of its ``EPM Fiscal Year``; an undeclared year or period is refused with
PeriodNotDeclared, never treated as Open. A period's effective status is the
stricter of its own status and its year's (fiscal_status_model).
"""

import frappe

from konsol.fiscal_status_model import (  # noqa: F401
    CLOSED,
    LOCKED,
    OPEN,
    effective_status,
)

#: A period that is not Open refuses new work.
SETTLED = (CLOSED, LOCKED)


class PeriodNotDeclared(frappe.ValidationError):
    """The fiscal year, or the period within it, has not been declared."""


def _not_declared(message):
    frappe.throw(message, PeriodNotDeclared)


def period_row(fiscal_year, fiscal_period) -> dict:
    """The declared period: code, type, dates, its own status, its year's
    status and the effective status. Raises PeriodNotDeclared when the year
    or the period is missing.

    The year is read LOCK IN SHARE MODE, so a concurrent close of the year
    waits for (or is seen by) the work this check guards.
    """
    if fiscal_year in (None, "") or fiscal_period in (None, ""):
        _not_declared(frappe._("No fiscal year and period given."))
    try:
        year = int(fiscal_year)
        period = int(fiscal_period)
    except (TypeError, ValueError):
        _not_declared(frappe._("FY{0} period {1} is not a fiscal period.").format(
            fiscal_year, fiscal_period))

    years = frappe.db.sql(
        "SELECT name, status FROM `tabEPM Fiscal Year` "
        "WHERE fiscal_year=%s LOCK IN SHARE MODE",
        (year,),
        as_dict=True,
    )
    if not years:
        _not_declared(frappe._("FY{0} is not declared: create it in EPM Fiscal Year.").format(year))
    year_doc = years[0]

    rows = frappe.db.sql(
        "SELECT period_code, period_type, start_date, end_date, status "
        "FROM `tabEPM Fiscal Year Period` "
        "WHERE parent=%s AND parentfield='periods' AND fiscal_period=%s",
        (year_doc["name"], period),
        as_dict=True,
    )
    if not rows:
        _not_declared(frappe._("FY{0} has no period {1}.").format(year, period))
    row = rows[0]

    return {
        "fiscal_year": year,
        "fiscal_period": period,
        "code": row["period_code"],
        "type": row["period_type"],
        "start_date": row["start_date"],
        "end_date": row["end_date"],
        "row_status": row["status"],
        "year_status": year_doc["status"],
        "status": effective_status(year_doc["status"], row["status"]),
    }


def get_status(fiscal_year, fiscal_period) -> str:
    """Effective status of one declared period. Undeclared raises."""
    return period_row(fiscal_year, fiscal_period)["status"]


def is_open(fiscal_year, fiscal_period) -> bool:
    return get_status(fiscal_year, fiscal_period) == OPEN


def assert_declared(fiscal_year, fiscal_period):
    """Refuse a year or period that has not been declared."""
    period_row(fiscal_year, fiscal_period)


#: Period type -> the EPM Settings check that lets it take trial balances.
#: "Regular" is not listed: it always takes them.
_TB_SETTING = {
    "Opening": "tb_accepts_opening",
    "Closing": "tb_accepts_closing",
    "Adjustment": "tb_accepts_adjustment",
}


def postable_types() -> set:
    """Period types that take trial balances on this site: Regular, plus each
    type whose EPM Settings check is ticked."""
    types = {"Regular"}
    for period_type, fieldname in _TB_SETTING.items():
        if frappe.db.get_single_value("EPM Settings", fieldname):
            types.add(period_type)
    return types


def assert_postable(fiscal_year, fiscal_period):
    """Refuse an undeclared period (PeriodNotDeclared), or one whose type does
    not take trial balances on this site. Does not check open/closed: callers
    call assert_open for that."""
    row = period_row(fiscal_year, fiscal_period)
    if row["type"] in postable_types():
        return
    frappe.throw(
        frappe._("{0} ({1}) does not take trial balances on this site. "
                 "Tick it in EPM Settings → Close to allow it.").format(
            row["code"], row["type"]),
        frappe.ValidationError,
    )


def period_dates(fiscal_year, fiscal_period):
    """(start_date, end_date) of one declared period."""
    row = period_row(fiscal_year, fiscal_period)
    return row["start_date"], row["end_date"]


def assert_open(fiscal_year, fiscal_period, action="run"):
    """Refuse work against an undeclared period, or one that has been closed off.

    Called from the run-start paths. The message names the period and the
    status so an operator can tell the difference between "I picked the wrong
    period" and "someone closed this while I was working".
    """
    status = get_status(fiscal_year, fiscal_period)
    if status == OPEN:
        return
    frappe.throw(
        frappe._("Cannot {0}: fiscal period {1} of FY{2} is {3}.").format(
            action, fiscal_period, fiscal_year, status.lower()
        ),
        frappe.ValidationError,
    )


#: Status -> the EPM Fiscal Year action that moves a period row there.
_ACTIONS = {CLOSED: "close_period", LOCKED: "lock_period", OPEN: "reopen_period"}


def set_status(fiscal_year, fiscal_period, status, start_date=None, end_date=None,
               reason=None, note=None):
    """Close, lock or reopen one declared period through the EPM Fiscal Year
    actions (close_period / lock_period / reopen_period), so their role
    checks, group-rate gate, stamping and notes apply. Reopening needs a
    ``reason`` (or ``note``). ``start_date`` / ``end_date``, when given, must
    equal the declared row's dates: the period's dates are edited on the
    EPM Fiscal Year, never here.

    Returns a frappe._dict with the attributes callers read off the old
    Period Status doc: name, fiscal_year, fiscal_period, status, closed_by,
    closed_on (plus period_code, start_date, end_date).
    """
    from frappe.utils import getdate

    action = _ACTIONS.get(status)
    if action is None:
        frappe.throw(frappe._("Unknown period status: {0}").format(status))
    try:
        period = int(fiscal_period)
    except (TypeError, ValueError):
        _not_declared(frappe._("FY{0} period {1} is not a fiscal period.").format(
            fiscal_year, fiscal_period))

    try:
        year = frappe.get_doc("EPM Fiscal Year", str(fiscal_year))
    except frappe.DoesNotExistError:
        _not_declared(frappe._("FY{0} is not declared: create it in EPM Fiscal Year.").format(
            fiscal_year))

    def _row():
        for r in year.periods or []:
            if r.fiscal_period not in (None, "") and int(r.fiscal_period) == period:
                return r
        return None

    row = _row()
    if row is None:
        _not_declared(frappe._("FY{0} has no period {1}.").format(fiscal_year, period))

    declared = (getdate(row.start_date), getdate(row.end_date))
    for given, have in ((start_date, declared[0]), (end_date, declared[1])):
        if given not in (None, "") and getdate(given) != have:
            frappe.throw(frappe._(
                "{0} of FY{1} is declared {2}..{3}; set_status can't change a "
                "period's dates — edit the EPM Fiscal Year").format(
                row.period_code, year.fiscal_year, declared[0], declared[1]))

    text = reason or note
    if status == OPEN:
        if not (text or "").strip():
            frappe.throw(frappe._(
                "Give a reason to reopen {0} of FY{1}: set_status(..., reason=...).").format(
                row.period_code, year.fiscal_year))
        year.reopen_period(period, text)
    else:
        getattr(year, action)(period, text)

    row = _row() or row
    return frappe._dict(
        name=row.name,
        fiscal_year=str(fiscal_year),
        fiscal_period=period,
        period_code=row.period_code,
        start_date=row.start_date,
        end_date=row.end_date,
        status=row.status,
        closed_by=row.closed_by,
        closed_on=row.closed_on,
    )


# Declared periods: the rows of each EPM Fiscal Year, joined to their year.
_DECLARED = (
    "FROM `tabEPM Fiscal Year Period` p "
    "JOIN `tabEPM Fiscal Year` y ON y.name = p.parent "
    "WHERE p.parentfield = 'periods'"
)


def first_period_affected(date):
    """The first period a date-keyed record changes: the start date of the
    first declared period starting on or after ``date``, across all years.
    None past the last declared period, or with no date.

    The warehouse applies a record to the periods starting on or after its
    date (#143 review); the periods are the declared rows, so a non-calendar
    or adjustment period is followed as declared, never guessed by month.
    """
    from frappe.utils import getdate

    if not date:
        return None
    rows = frappe.db.sql(
        f"SELECT p.start_date {_DECLARED} AND p.start_date >= %(date)s "
        "ORDER BY p.start_date LIMIT 1",
        {"date": getdate(date)},
        as_dict=True,
    )
    return getdate(rows[0]["start_date"]) if rows else None


def assert_open_between(start_date, end_date=None, action="run", end_exclusive=False):
    """Refuse unless every declared period a date-keyed record affects is
    effectively Open (the stricter of the period's status and its year's).
    Those are the periods starting on or after ``start_date`` (as in
    first_period_affected: a period's membership is decided by its first
    day, so a change effective 15 March leaves March alone) and starting on
    or before ``end_date`` (before it with ``end_exclusive``), or every
    later period when there is no end.

    Gating one period wasn't enough: an ownership period or an equity rate
    changes every period it covers, so a cancel with only its first period
    open rewrote the closed ones after it (#143 review). The years are read
    LOCK IN SHARE MODE, like period_row, so a concurrent close waits.
    """
    from frappe.utils import getdate

    if not start_date:
        return
    where = [
        "p.start_date >= %(start)s",
        "(y.status <> %(open)s OR p.status <> %(open)s)",
    ]
    params = {"start": getdate(start_date), "open": OPEN}
    if end_date:
        where.append(f"p.start_date {'<' if end_exclusive else '<='} %(end)s")
        params["end"] = getdate(end_date)
    rows = frappe.db.sql(
        "SELECT y.fiscal_year, p.period_code, y.status AS year_status, p.status AS row_status "
        f"{_DECLARED} AND {' AND '.join(where)} "
        "ORDER BY p.start_date, y.fiscal_year, p.fiscal_period LIMIT 1 LOCK IN SHARE MODE",
        params,
        as_dict=True,
    )
    if rows:
        r = rows[0]
        status = effective_status(r["year_status"], r["row_status"])
        frappe.throw(
            frappe._("Cannot {0}: it changes fiscal period {1} of FY{2}, which is {3}.").format(
                action, r["period_code"], r["fiscal_year"], status.lower()
            ),
            frappe.ValidationError,
        )

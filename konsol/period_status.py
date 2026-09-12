"""Period close state — the one place that answers "is this period open?".

``Period Status`` records are created on demand: a period nobody has closed has
no record, and is Open. That keeps the fourteen-periods-times-N-years grid from
having to be pre-populated, and means an upgrade needs no backfill.
"""

import frappe

from konsol.epm.doctype.period_status.period_status import (  # noqa: F401
    CLOSED,
    LOCKED,
    OPEN,
    SETTLED,
)


def _name(fiscal_year, fiscal_period):
    return f"PS-{fiscal_year}-{int(fiscal_period)}"


def get_status(fiscal_year, fiscal_period) -> str:
    """Status of one period. Absent record means Open — never closed, so open."""
    if not fiscal_year or fiscal_period in (None, ""):
        return OPEN
    try:
        period = int(fiscal_period)
    except (TypeError, ValueError):
        return OPEN
    status = frappe.db.get_value(
        "Period Status",
        {"fiscal_year": str(fiscal_year), "fiscal_period": period},
        "status",
    )
    return status or OPEN


def is_open(fiscal_year, fiscal_period) -> bool:
    return get_status(fiscal_year, fiscal_period) == OPEN


def assert_open(fiscal_year, fiscal_period, action="run"):
    """Refuse work against a period that has been closed off.

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


def set_status(fiscal_year, fiscal_period, status, start_date=None, end_date=None):
    """Create or update the record for one period. Returns the saved doc."""
    fiscal_year = str(fiscal_year)
    fiscal_period = int(fiscal_period)
    name = _name(fiscal_year, fiscal_period)

    if frappe.db.exists("Period Status", name):
        doc = frappe.get_doc("Period Status", name)
    else:
        doc = frappe.new_doc("Period Status")
        doc.fiscal_year = fiscal_year
        doc.fiscal_period = fiscal_period

    doc.status = status
    if start_date is not None:
        doc.start_date = start_date
    if end_date is not None:
        doc.end_date = end_date
    doc.save()
    return doc


# The warehouse's period start (dbt build_date_from_year_period): period P of
# year Y is the month starting Y-P-01.
_PERIOD_START = "STR_TO_DATE(CONCAT(fiscal_year, '-', LPAD(fiscal_period, 2, '0'), '-01'), '%%Y-%%m-%%d')"


def first_period_affected(date):
    """The first period a date-keyed record changes, as the warehouse applies it.

    The warehouse applies a record to the periods whose start (the 1st of the
    month) is on or after its date. So a record dated the 1st first affects
    that month, and one dated later in the month first affects the next
    (#143 review). If build_date_from_year_period ever learns a non-calendar
    fiscal year, this must follow it.
    """
    import datetime

    from frappe.utils import getdate

    d = getdate(date) if date else None
    if d is None:
        return None
    if d.day == 1:
        return d
    return (d.replace(day=1) + datetime.timedelta(days=32)).replace(day=1)


def assert_open_between(start_date, end_date=None, action="run", end_exclusive=False):
    """Refuse when any Closed or Locked period falls in the range a date-keyed
    record affects: from the first period ``start_date`` affects, up to
    ``end_date`` (the periods starting on or before it; before it with
    ``end_exclusive``), or open-ended when there is no end.

    Gating one month wasn't enough: an ownership period or an equity rate
    changes every month it covers, so a cancel with only its first month open
    rewrote the closed months after it (#143 review).
    """
    from frappe.utils import getdate

    first = first_period_affected(start_date)
    if first is None:
        return
    where = [f"status IN %(settled)s", f"{_PERIOD_START} >= %(first)s"]
    params = {"settled": tuple(SETTLED), "first": first}
    if end_date:
        where.append(f"{_PERIOD_START} {'<' if end_exclusive else '<='} %(end)s")
        params["end"] = getdate(end_date)
    rows = frappe.db.sql(
        f"SELECT fiscal_year, fiscal_period, status FROM `tabPeriod Status` "
        f"WHERE {' AND '.join(where)} ORDER BY {_PERIOD_START} LIMIT 1",
        params,
        as_dict=True,
    )
    if rows:
        r = rows[0]
        frappe.throw(
            frappe._("Cannot {0}: it changes fiscal period {1} of FY{2}, which is {3}.").format(
                action, r.fiscal_period, r.fiscal_year, str(r.status).lower()
            ),
            frappe.ValidationError,
        )

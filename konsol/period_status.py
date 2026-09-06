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

"""Fiscal year/period status precedence, pure: no frappe import.

A period's effective status is the stricter of its own status and the
status of the fiscal year it belongs to.
"""

OPEN = "Open"
CLOSED = "Closed"
LOCKED = "Locked"

_RANK = {OPEN: 0, CLOSED: 1, LOCKED: 2}


def effective_status(year_status, row_status):
    for status in (year_status, row_status):
        if status not in _RANK:
            raise ValueError(f"unknown fiscal status {status!r}")
    return year_status if _RANK[year_status] >= _RANK[row_status] else row_status

"""Fiscal year period patterns (konsol#189), pure: no frappe import.

Generates the period rows for a fiscal year given only its start and end
dates. Today this covers the "Monthly (12)" pattern; opening/closing
periods and other patterns (e.g. 4-4-5, quarterly) are later work.
"""
import calendar
import datetime

PERIODS_PER_YEAR = 12
MONTHS_PER_PERIOD = 1


def _add_months(base, n):
    """base plus n calendar months, clipping the day to the target month's length."""
    total = base.month - 1 + n
    year = base.year + total // 12
    month = total % 12 + 1
    day = min(base.day, calendar.monthrange(year, month)[1])
    return datetime.date(year, month, day)


def _months_between(start_date, end_date):
    """Best-effort count of months from start_date to end_date (inclusive), for
    error messages only: the n where start_date + n months lands the day after
    end_date, searched around the naive year/month difference in case the span
    isn't calendar-aligned."""
    target = end_date + datetime.timedelta(days=1)
    guess = (target.year - start_date.year) * 12 + (target.month - start_date.month)
    for candidate in range(max(guess - 2, 0), guess + 3):
        if _add_months(start_date, candidate) == target:
            return candidate
    return guess


def monthly_periods(start_date, end_date):
    """The 12 period rows of a "Monthly (12)" fiscal year running from
    start_date to end_date (inclusive), both datetime.date.

    Periods are contiguous month-length spans anchored on start_date's
    day-of-month (so a year starting 2025-04-06 has periods starting on the
    6th of each month), each ending the day before the next starts; the last
    period ends on end_date. Refuses (ValueError) a span that isn't exactly
    12 months long, naming the number of months found.
    """
    expected_end = _add_months(start_date, PERIODS_PER_YEAR) - datetime.timedelta(days=1)
    if expected_end != end_date:
        found = _months_between(start_date, end_date)
        raise ValueError(
            "Monthly (12) needs a fiscal year exactly 12 months long; found %s "
            "months (%s to %s)" % (found, start_date, end_date)
        )

    rows = []
    for period in range(1, PERIODS_PER_YEAR + 1):
        p_start = _add_months(start_date, period - 1)
        if period == PERIODS_PER_YEAR:
            p_end = end_date
        else:
            p_end = _add_months(start_date, period) - datetime.timedelta(days=1)
        rows.append({
            "period": period,
            "code": "P%02d" % period,
            "label": "%s %d" % (calendar.month_abbr[p_start.month], p_start.year),
            "type": "Regular",
            "start_date": p_start,
            "end_date": p_end,
            "quarter": "Q%d" % (((period - 1) // 3) + 1),
        })
    return rows

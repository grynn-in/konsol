"""Fiscal year period patterns (konsol#189), pure: no frappe import.

Generates the period rows for a fiscal year given only its start and end
dates. Today this covers the "Monthly (12)" and "13 Periods (4 Weeks)"
patterns; opening/closing periods and other patterns (e.g. 4-4-5,
quarterly) are later work.
"""
import calendar
import datetime

PERIODS_PER_YEAR = 12
MONTHS_PER_PERIOD = 1

THIRTEEN_PERIOD_COUNT = 13
#: Day counts a "13 Periods (4 Weeks)" fiscal year may run: 13 * 28 = 364
#: days in a normal year, or 371 (53 weeks) in a leap week year, where the
#: extra 7 days land on the last period (P13 -> 35 days) rather than being
#: spread across all of them.
THIRTEEN_PERIOD_YEAR_LENGTHS = {
    364: [28] * THIRTEEN_PERIOD_COUNT,
    371: [28] * (THIRTEEN_PERIOD_COUNT - 1) + [35],
}


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


def thirteen_periods(start_date, end_date):
    """The 13 period rows of a "13 Periods (4 Weeks)" fiscal year running
    from start_date to end_date (inclusive), both datetime.date.

    Periods are contiguous 28-day (4-week) spans starting at start_date. A
    364-day year (the common case) gives 13 periods of 28 days each; a
    371-day year (53 weeks) gives P01..P12 of 28 days and a 35-day P13 that
    absorbs the extra week. There is no quarter grouping for this pattern
    (13 doesn't divide by 3), so "quarter" is blank on every row. Refuses
    (ValueError) any other span length, naming the number of days found.
    """
    total_days = (end_date - start_date).days + 1
    period_lengths = THIRTEEN_PERIOD_YEAR_LENGTHS.get(total_days)
    if period_lengths is None:
        raise ValueError(
            "13 Periods (4 Weeks) needs a fiscal year of 364 or 371 days; "
            "found %s days (%s to %s)" % (total_days, start_date, end_date)
        )

    rows = []
    p_start = start_date
    for period, length in enumerate(period_lengths, start=1):
        p_end = p_start + datetime.timedelta(days=length - 1)
        code = "P%02d" % period
        rows.append({
            "period": period,
            "code": code,
            "label": "%s %d" % (code, start_date.year),
            "type": "Regular",
            "start_date": p_start,
            "end_date": p_end,
            "quarter": "",
        })
        p_start = p_end + datetime.timedelta(days=1)
    return rows

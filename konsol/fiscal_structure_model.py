"""Fiscal year period-row rules (konsol#189), pure: no frappe or konsol import.

A fiscal year's periods are given as a list of dicts, each with at least
`period` (an integer position, 0..255) and `code` (a short label). This
checks the whole list at once and names every offending row, rather than
stopping at the first problem.
"""
from datetime import date

MIN_PERIOD = 0
MAX_PERIOD = 255

MIN_YEAR = 1970
MAX_YEAR = 2148

# The range a ClickHouse Date can hold; it clamps silently outside it.
MIN_DATE = date(1970, 1, 1)
MAX_DATE = date(2149, 6, 6)


def period_problems(rows):
    """Return a list of error strings for `rows`; empty means the year is valid."""
    errors = []
    by_period = {}
    by_code = {}

    for i, r in enumerate(rows):
        row_no = i + 1
        period = r.get("period")
        code = r.get("code")

        if not isinstance(period, int) or isinstance(period, bool):
            errors.append(f"Row {row_no}: period {period!r} is not an integer")
        elif period < MIN_PERIOD or period > MAX_PERIOD:
            errors.append(f"Row {row_no}: period {period} is outside {MIN_PERIOD}..{MAX_PERIOD}")
        else:
            by_period.setdefault(period, []).append(row_no)

        code_str = code.strip() if isinstance(code, str) else code
        if not code_str:
            errors.append(f"Row {row_no}: code is blank")
        else:
            by_code.setdefault(code_str.lower(), []).append((row_no, code_str))

    for period, row_nos in by_period.items():
        if len(row_nos) > 1:
            errors.append(f"Period {period} is used by rows {_join(row_nos)}")

    for _, entries in by_code.items():
        if len(entries) > 1:
            row_nos = [row_no for row_no, _ in entries]
            display_code = entries[0][1]
            errors.append(f"Code {display_code!r} is used by rows {_join(row_nos)}")

    return errors


def year_problems(year, rows):
    """Return a list of error strings for a fiscal `year` dict and its `rows`.

    `year` has `year` (integer), `start_date` and `end_date` (datetime.date).
    Each row in `rows` has `start_date`, `end_date` and `code`. Empty means
    the year and its rows are valid.
    """
    errors = []

    yr = year.get("year")
    if not isinstance(yr, int) or isinstance(yr, bool):
        errors.append(f"Year {yr!r} is not an integer")
    elif yr < MIN_YEAR or yr > MAX_YEAR:
        errors.append(f"Year {yr} is outside {MIN_YEAR}..{MAX_YEAR}")

    start = year.get("start_date")
    end = year.get("end_date")

    for label, d in (("start_date", start), ("end_date", end)):
        if d is not None and (d < MIN_DATE or d > MAX_DATE):
            errors.append(f"Year {label} {d} is outside {MIN_DATE}..{MAX_DATE}")

    year_has_valid_range = False
    if start is not None and end is not None:
        if start >= end:
            errors.append(f"Year start_date {start} is not before end_date {end}")
        else:
            year_has_valid_range = True

    for i, r in enumerate(rows):
        row_no = i + 1
        code = r.get("code")
        r_start = r.get("start_date")
        r_end = r.get("end_date")

        for label, d in (("start_date", r_start), ("end_date", r_end)):
            if d is not None and (d < MIN_DATE or d > MAX_DATE):
                errors.append(f"Row {row_no} ({code!r}): {label} {d} is outside {MIN_DATE}..{MAX_DATE}")

        if r_start is not None and r_end is not None and r_start > r_end:
            errors.append(f"Row {row_no} ({code!r}): start_date {r_start} is after end_date {r_end}")

        if year_has_valid_range:
            if r_start is not None and (r_start < start or r_start > end):
                errors.append(
                    f"Row {row_no} ({code!r}): start_date {r_start} is outside the year {start}..{end}"
                )
            if r_end is not None and (r_end < start or r_end > end):
                errors.append(
                    f"Row {row_no} ({code!r}): end_date {r_end} is outside the year {start}..{end}"
                )

    return errors


def _join(row_nos):
    """Format 1-based row numbers as "2 and 5" or "2, 5 and 9"."""
    row_nos = sorted(row_nos)
    if len(row_nos) == 2:
        return f"{row_nos[0]} and {row_nos[1]}"
    return ", ".join(str(n) for n in row_nos[:-1]) + f" and {row_nos[-1]}"

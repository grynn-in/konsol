"""Fiscal year period-row rules (konsol#189), pure: no frappe or konsol import.

A fiscal year's periods are given as a list of dicts, each with at least
`period` (an integer position, 0..255) and `code` (a short label). This
checks the whole list at once and names every offending row, rather than
stopping at the first problem.
"""
from datetime import date, timedelta

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


def regular_period_problems(year, rows):
    """Return error strings unless the Regular rows of `rows` tile `year` exactly.

    Only rows with `type == "Regular"` are checked (Opening, Closing and
    Adjustment rows are ignored). They must exist, be numbered 1..n with no
    gaps, and, in period order, run from the year's start_date to its
    end_date with each one starting the day after the previous one ends.
    """
    regular = [r for r in rows if r.get("type") == "Regular"]
    if not regular:
        return ["No Regular periods"]

    errors = []

    numbered = [r for r in regular
                if isinstance(r.get("period"), int) and not isinstance(r.get("period"), bool)]
    numbers = sorted(r["period"] for r in numbered)
    missing = sorted(set(range(1, len(numbers) + 1)) - set(numbers))
    if numbers != list(range(1, len(numbers) + 1)):
        listed = ", ".join(str(n) for n in numbers)
        if missing:
            what = ", ".join(str(n) for n in missing)
            verb = "is" if len(missing) == 1 else "are"
            errors.append(f"Regular periods are numbered {listed}: {what} {verb} missing")
        else:
            errors.append(f"Regular periods are numbered {listed}: they must run 1..{len(numbers)}")

    dated = [r for r in numbered if r.get("start_date") is not None and r.get("end_date") is not None]
    if not dated:
        return errors
    dated.sort(key=lambda r: (r["period"], r["start_date"]))

    y_start = year.get("start_date")
    y_end = year.get("end_date")
    first, last = dated[0], dated[-1]

    if y_start is not None and first["start_date"] != y_start:
        side = "after" if first["start_date"] > y_start else "before"
        errors.append(f"The first Regular period {first.get('code')} starts {first['start_date']}, "
                      f"{side} the year start {y_start}")

    for prev, cur in zip(dated, dated[1:]):
        day_after = prev["end_date"] + timedelta(days=1)
        if cur["start_date"] > day_after:
            gap = (cur["start_date"] - day_after).days
            unit = "day" if gap == 1 else "days"
            errors.append(f"{cur.get('code')} starts {cur['start_date']} but {prev.get('code')} "
                          f"ends {prev['end_date']}: a gap of {gap} {unit}")
        elif cur["start_date"] < day_after:
            errors.append(f"{cur.get('code')} overlaps {prev.get('code')}: {cur.get('code')} starts "
                          f"{cur['start_date']} but {prev.get('code')} ends {prev['end_date']}")

    if y_end is not None and last["end_date"] != y_end:
        side = "before" if last["end_date"] < y_end else "after"
        errors.append(f"The last Regular period {last.get('code')} ends {last['end_date']}, "
                      f"{side} the year end {y_end}")

    return errors


KNOWN_TYPES = {"Opening", "Regular", "Closing", "Adjustment"}


def placement_problems(year, rows):
    """Return error strings for the placement of Opening/Closing/Adjustment rows.

    Regular rows are checked by `regular_period_problems`; this checks the
    other three types against `year` and against the Regular periods:

    - Opening: at most one; it must be period 0 (and no other type may use
      0); it is exactly one day, the year's start_date.
    - Closing: at most one; its period is above every Regular period; it is
      exactly one day, the year's end_date.
    - Adjustment: any number; each period is above every Regular period;
      each lies inside the year.

    A row whose `type` is none of Opening/Regular/Closing/Adjustment is
    refused by name. Each error names the offending row's code.
    """
    errors = []

    y_start = year.get("start_date")
    y_end = year.get("end_date")

    def is_period_number(p):
        return isinstance(p, int) and not isinstance(p, bool)

    regular_periods = [r["period"] for r in rows
                        if r.get("type") == "Regular" and is_period_number(r.get("period"))]
    max_regular = max(regular_periods) if regular_periods else None

    for r in rows:
        if r.get("type") not in KNOWN_TYPES:
            errors.append(f"Row {r.get('code')!r}: unknown period type {r.get('type')!r}")

    openings = [r for r in rows if r.get("type") == "Opening"]
    closings = [r for r in rows if r.get("type") == "Closing"]
    adjustments = [r for r in rows if r.get("type") == "Adjustment"]

    if len(openings) > 1:
        codes = ", ".join(repr(r.get("code")) for r in openings)
        errors.append(f"Only one Opening period is allowed: {codes}")

    for r in openings:
        code = r.get("code")
        period = r.get("period")
        if period != 0:
            errors.append(f"Opening period {code!r} must be period 0, not {period!r}")
        start, end = r.get("start_date"), r.get("end_date")
        if y_start is not None and (start != y_start or end != y_start):
            errors.append(f"Opening period {code!r} must be one day, the year start {y_start}")

    for r in rows:
        if r.get("type") != "Opening" and r.get("period") == 0:
            errors.append(f"Period 0 is reserved for Opening, but row {r.get('code')!r} "
                          f"is {r.get('type')!r}")

    if len(closings) > 1:
        codes = ", ".join(repr(r.get("code")) for r in closings)
        errors.append(f"Only one Closing period is allowed: {codes}")

    for r in closings:
        code = r.get("code")
        period = r.get("period")
        if max_regular is not None and is_period_number(period) and period <= max_regular:
            errors.append(f"Closing period {code!r} (period {period}) must be above every "
                          f"Regular period (highest is {max_regular})")
        start, end = r.get("start_date"), r.get("end_date")
        if y_end is not None and (start != y_end or end != y_end):
            errors.append(f"Closing period {code!r} must be one day, the year end {y_end}")

    for r in adjustments:
        code = r.get("code")
        period = r.get("period")
        if max_regular is not None and is_period_number(period) and period <= max_regular:
            errors.append(f"Adjustment period {code!r} (period {period}) must be above every "
                          f"Regular period (highest is {max_regular})")
        start, end = r.get("start_date"), r.get("end_date")
        if y_start is not None and start is not None and start < y_start:
            errors.append(f"Adjustment period {code!r} starts {start} before the year start {y_start}")
        if y_end is not None and end is not None and end > y_end:
            errors.append(f"Adjustment period {code!r} ends {end} after the year end {y_end}")

    return errors


def _join(row_nos):
    """Format 1-based row numbers as "2 and 5" or "2, 5 and 9"."""
    row_nos = sorted(row_nos)
    if len(row_nos) == 2:
        return f"{row_nos[0]} and {row_nos[1]}"
    return ", ".join(str(n) for n in row_nos[:-1]) + f" and {row_nos[-1]}"

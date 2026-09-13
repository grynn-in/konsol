"""Fiscal year period-row rules (konsol#189), pure: no frappe or konsol import.

A fiscal year's periods are given as a list of dicts, each with at least
`period` (an integer position, 0..255) and `code` (a short label). This
checks the whole list at once and names every offending row, rather than
stopping at the first problem.
"""
MIN_PERIOD = 0
MAX_PERIOD = 255


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


def _join(row_nos):
    """Format 1-based row numbers as "2 and 5" or "2, 5 and 9"."""
    row_nos = sorted(row_nos)
    if len(row_nos) == 2:
        return f"{row_nos[0]} and {row_nos[1]}"
    return ", ".join(str(n) for n in row_nos[:-1]) + f" and {row_nos[-1]}"

"""Period strips rule, pure (no frappe): what the Year and Period slider
strips on the Desk period reports need.

``strip_info(period_rows, latest_with_data)``: ``period_rows`` are the dicts of
``fiscal_calendar.fiscal_period_rows()``; ``latest_with_data`` is a
``(fiscal_year, fiscal_period)`` pair or None.

``latest`` is ``latest_with_data`` when that period is declared, else the
latest declared Regular period, else None.
"""

YEAR_MIN = 1980
YEAR_MAX = 2045
PERIOD_MIN = 1
PERIOD_MAX = 12


def strip_info(period_rows, latest_with_data):
    declared = {(int(r["fiscal_year"]), int(r["fiscal_period"])) for r in period_rows}
    years = sorted({fy for fy, _ in declared})
    latest = None
    if latest_with_data is not None:
        key = (int(latest_with_data[0]), int(latest_with_data[1]))
        if key in declared:
            latest = key
    if latest is None:
        regular = [(int(r["fiscal_year"]), int(r["fiscal_period"])) for r in period_rows
                   if r.get("period_type") == "Regular"]
        latest = max(regular) if regular else None
    return {
        "years": years,
        "latest": ({"fiscal_year": latest[0], "fiscal_period": latest[1]}
                   if latest else None),
        "year_min": YEAR_MIN,
        "year_max": YEAR_MAX,
        "period_min": PERIOD_MIN,
        "period_max": PERIOD_MAX,
    }

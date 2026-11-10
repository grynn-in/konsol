"""Period strips rule, pure (no frappe): what the Year and Period slider
strips on the Desk period reports need.

``strip_info(period_rows, periods_with_data)``: ``period_rows`` are the dicts of
``fiscal_calendar.fiscal_period_rows()``; ``periods_with_data`` is an iterable
of ``(fiscal_year, fiscal_period)`` pairs that have data (or None).

``latest`` is the newest period with data that is declared Regular (P0, P13
and other non-Regular periods never count), else the latest declared Regular
period, else None.
"""

YEAR_MIN = 1980
YEAR_MAX = 2045
PERIOD_MIN = 0   # Open (opening balances)
PERIOD_MAX = 13  # Close (closing adjustments)


def strip_info(period_rows, periods_with_data):
    declared = {(int(r["fiscal_year"]), int(r["fiscal_period"])) for r in period_rows}
    years = sorted({fy for fy, _ in declared})
    regular = {(int(r["fiscal_year"]), int(r["fiscal_period"])) for r in period_rows
               if r.get("period_type") == "Regular"}
    with_data = {(int(fy), int(fp)) for fy, fp in (periods_with_data or [])}
    candidates = with_data & regular
    latest = max(candidates) if candidates else (max(regular) if regular else None)
    return {
        "years": years,
        "latest": ({"fiscal_year": latest[0], "fiscal_period": latest[1]}
                   if latest else None),
        "year_min": YEAR_MIN,
        "year_max": YEAR_MAX,
        "period_min": PERIOD_MIN,
        "period_max": PERIOD_MAX,
    }

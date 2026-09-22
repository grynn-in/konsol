"""Resolving a read token against a declared fiscal calendar, and the
aggregation a measure declares (konsol#251).

Pure: imports nothing from frappe or konsol, so the rules below are testable
without a site.

Two decisions live here.

**Which periods a token means.** The accepted set is the customer's
declaration -- the EPM Fiscal Year Period rows for that year -- not a constant
in this app. A monthly customer declares OPN, P01-P12, CLS; a 4-week customer
declares P01-P13 and a close at 14. The same token answers correctly for both
because nothing here assumes twelve. An undeclared period, or an undeclared
year, is refused naming what IS declared; there is no 1-12 fallback, which is
the failure konsol#210 and konsolidat#235 describe from the other side.

``FY`` is the Regular periods only. The closing period is the P&L reversal, so
including it would zero out every P&L account; a reader who wants the year-end
position asks for the closing period by number or by code (``13``/``CLS``) and
reads a measure that carries a balance.

**How periods combine.** A measure declares its aggregation (Measure.cube_type)
and the read path must honour it. Summing a cumulative balance across periods
is meaningless -- it was returning the sum of six balances for an FY read --
so ``last`` takes the value at the highest period that has a row.
"""

# Period types, as EPM Fiscal Year Period declares them.
OPENING = "Opening"
REGULAR = "Regular"
CLOSING = "Closing"
ADJUSTMENT = "Adjustment"

# Tokens that mean "a span of periods" rather than one period.
_HALVES = {"H1": ("Q1", "Q2"), "H2": ("Q3", "Q4")}
_QUARTERS = ("Q1", "Q2", "Q3", "Q4")

# Aggregation -> the ClickHouse expression that produces one value per period.
# `last` carries the period with it so the highest one wins across the range.
_SQL_AGGREGATES = {
    "sum": "sum({col})",
    "avg": "avg({col})",
    "count": "count({col})",
    "last": "argMax({col}, fiscal_period)",
}


def _declared_periods(rows):
    """[(fiscal_period, code, type, quarter)] sorted, from calendar rows."""
    out = []
    for r in rows:
        out.append((
            int(r["fiscal_period"]),
            str(r.get("period_code") or "").strip().upper(),
            str(r.get("period_type") or "").strip(),
            str(r.get("quarter") or "").strip().upper(),
        ))
    return sorted(out)


def _refuse(token, declared):
    numbers = ", ".join(str(p) for p, _, _, _ in declared)
    codes = ", ".join(c for _, c, _, _ in declared if c)
    raise ValueError(
        f"Period '{token}' is not declared for this fiscal year. "
        f"Declared periods: {numbers}"
        + (f" (codes: {codes})" if codes else "")
        + ". Q1-Q4, H1-H2 and FY are available where the calendar declares "
          "the quarters they span."
    )


def resolve_period(period, calendar_rows):
    """Resolve a read token to a tuple of fiscal_period integers.

    ``calendar_rows`` are that year's declared periods: dicts carrying
    ``fiscal_period``, ``period_code``, ``period_type`` and ``quarter``.

    Accepts a period number, a declared period code (``P07``, ``CLS``,
    ``OPN``), a declared quarter (``Q1``-``Q4``), a half (``H1``/``H2``) or
    ``FY``. Raises ValueError naming the declared set on anything else.
    """
    declared = _declared_periods(calendar_rows)
    if not declared:
        raise ValueError(
            "No fiscal periods are declared for this year, so a read cannot "
            "resolve a period. Declare the year's calendar on EPM Fiscal Year."
        )

    token = str(period).strip().upper()

    if token == "FY":
        return tuple(p for p, _, t, _ in declared if t == REGULAR)

    if token in _HALVES:
        periods = tuple(
            p for p, _, t, q in declared
            if t == REGULAR and q in _HALVES[token]
        )
        if not periods:
            _refuse(period, declared)
        return periods

    if token in _QUARTERS:
        periods = tuple(
            p for p, _, t, q in declared if t == REGULAR and q == token
        )
        if not periods:
            _refuse(period, declared)
        return periods

    for p, code, _, _ in declared:
        if code and code == token:
            return (p,)

    try:
        wanted = int(token)
    except (TypeError, ValueError):
        _refuse(period, declared)

    for p, _, _, _ in declared:
        if p == wanted:
            return (p,)

    _refuse(period, declared)


def sql_aggregate(cube_type, column):
    """The ClickHouse aggregate for a measure's declared ``cube_type``.

    Refuses an aggregation it does not know rather than falling back to
    ``sum``: a measure whose declaration is not honoured reads as a number
    that looks right and is not.
    """
    key = str(cube_type or "").strip().lower()
    if key not in _SQL_AGGREGATES:
        raise ValueError(
            f"Unknown measure aggregation '{cube_type}'. "
            f"Declared measures must use one of: "
            f"{', '.join(sorted(_SQL_AGGREGATES))}."
        )
    return _SQL_AGGREGATES[key].format(col=column)


def combine_periods(cube_type, period_values):
    """Combine one value per period into the value for a cell.

    ``period_values`` is [(fiscal_period, value)] for the periods that
    actually returned a row. A period the warehouse holds no row for is
    absent, not zero, so it cannot drag an average down or become the
    ``last`` value.
    """
    key = str(cube_type or "").strip().lower()
    if key not in _SQL_AGGREGATES:
        raise ValueError(
            f"Unknown measure aggregation '{cube_type}'. "
            f"Declared measures must use one of: "
            f"{', '.join(sorted(_SQL_AGGREGATES))}."
        )
    if not period_values:
        return 0.0
    values = [v for _, v in period_values]
    if key in ("sum", "count"):
        return float(sum(values))
    if key == "avg":
        return float(sum(values)) / len(values)
    return float(max(period_values, key=lambda pv: pv[0])[1])

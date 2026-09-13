"""One-time migration from implied periods to declared Fiscal Years
(konsol#189), pure: no frappe import.

Today a (fiscal_year, fiscal_period) pair is implied: documents carry it and
Period Status rows hold its status. plan() works out what to declare so that
every such pair has a Fiscal Year row, and which Period Status statuses to
carry across. It changes nothing itself; the caller applies the plan.

Siblings are loaded by path (as the host tests load modules), so this module
works both imported as konsol.fiscal_migration_model and loaded from its file.
"""
import datetime
import importlib.util
import os

_HERE = os.path.dirname(os.path.abspath(__file__))


def _load_sibling(name):
    spec = importlib.util.spec_from_file_location(
        "_fmm_" + name, os.path.join(_HERE, name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_patterns = _load_sibling("fiscal_patterns_model")
_status = _load_sibling("fiscal_status_model")

PATTERN = "Monthly (12)"
CLOSING_PERIOD = 13
MIN_PERIOD = 0
MAX_PERIOD = 255


def _adjustment_row(period, year):
    day = datetime.date(year, 12, 31)
    code = "P%d" % period
    return {
        "period": period,
        "code": code,
        "label": "%s %d" % (code, year),
        "type": "Adjustment",
        "start_date": day,
        "end_date": day,
        "quarter": "",
    }


def _planned_year(year, periods, conflicts):
    """A calendar-year Monthly (12) Fiscal Year (OPN, P01..P12, CLS) plus one
    Adjustment row per period above 13 in `periods`; a period outside
    0..255 gets no row and a conflict instead."""
    start = datetime.date(year, 1, 1)
    end = datetime.date(year, 12, 31)
    rows = _patterns.generate_periods(PATTERN, start, end, True, True)
    for period in sorted(periods):
        if period < MIN_PERIOD or period > MAX_PERIOD:
            conflicts.append(
                "Fiscal year %d: period %d is outside %d..%d; no period row "
                "can be created for it." % (year, period, MIN_PERIOD, MAX_PERIOD))
        elif period > CLOSING_PERIOD:
            rows.append(_adjustment_row(period, year))
    for row in rows:
        row["status"] = _status.OPEN
    return {
        "year": year,
        "start_date": start,
        "end_date": end,
        "period_pattern": PATTERN,
        "rows": rows,
    }


def _date_str(value):
    return "-" if value is None else str(value)


def plan(used_pairs, ps_rows, existing_years):
    """What to declare for the move to Fiscal Years.

    used_pairs: set of (fiscal_year, fiscal_period) ints that documents use.
    ps_rows: Period Status rows (fiscal_year, fiscal_period, status,
        start_date, end_date, closed_by, closed_on).
    existing_years: year -> {"rows": [{period, code, start_date, end_date,
        status}]} for Fiscal Years already declared.

    Returns {"create": [year dicts], "moves": [{year, code, status,
    closed_by, closed_on}], "conflicts": [str]}. Running it again with the
    created years (moves applied) as existing_years plans nothing.
    """
    conflicts = []

    periods_by_year = {}
    for year, period in used_pairs:
        periods_by_year.setdefault(year, set()).add(period)
    for row in ps_rows:
        periods_by_year.setdefault(row["fiscal_year"], set()).add(row["fiscal_period"])

    create = []
    for year in sorted(periods_by_year):
        if year in existing_years:
            continue
        create.append(_planned_year(year, periods_by_year[year], conflicts))

    targets = {y["year"]: y["rows"] for y in create}
    for year, info in existing_years.items():
        targets[year] = info["rows"]
    planned = {y["year"] for y in create}

    for year, period in sorted(used_pairs):
        if year not in existing_years:
            continue  # a year the plan itself creates already covers used periods
        target = next((t for t in targets[year] if t["period"] == period), None)
        if target is None:
            conflicts.append(
                "Fiscal year %d period %d is used by documents but not "
                "declared in Fiscal Year %d." % (year, period, year))

    moves = []
    for row in sorted(ps_rows, key=lambda r: (r["fiscal_year"], r["fiscal_period"])):
        year = row["fiscal_year"]
        period = row["fiscal_period"]
        target = next((t for t in targets[year] if t["period"] == period), None)
        if target is None:
            if year in planned:
                continue  # out of range: already named when the year was planned
            conflicts.append(
                "Fiscal year %d has no period %d for its Period Status row "
                "(%s)." % (year, period, row["status"]))
            continue

        ps_start, ps_end = row.get("start_date"), row.get("end_date")
        if (ps_start is not None and ps_start != target["start_date"]) or (
                ps_end is not None and ps_end != target["end_date"]):
            conflicts.append(
                "Fiscal year %d period %d (%s): Period Status dates %s to %s "
                "differ from the Fiscal Year row's %s to %s." % (
                    year, period, target["code"],
                    _date_str(ps_start), _date_str(ps_end),
                    _date_str(target["start_date"]), _date_str(target["end_date"])))
            continue

        ps_status = row["status"] or _status.OPEN
        target_status = target.get("status") or _status.OPEN
        if target_status == ps_status:
            continue
        if target_status == _status.OPEN:
            moves.append({
                "year": year,
                "code": target["code"],
                "status": ps_status,
                "closed_by": row.get("closed_by"),
                "closed_on": row.get("closed_on"),
            })
            continue
        conflicts.append(
            "Fiscal year %d period %d (%s) is %s but its Period Status row is "
            "%s; it has changed since an earlier run." % (
                year, period, target["code"], target_status, ps_status))

    return {"create": create, "moves": moves, "conflicts": conflicts}

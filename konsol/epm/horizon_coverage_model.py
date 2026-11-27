"""Forecast horizon coverage, the pure rule (konsol#348 S6 KA8; QB10 (b) and
S6-Q9 (a), taken under the standing rule: a Desk report over MariaDB, no
warehouse read).

A forecast round is (scenario, as-of date) (Q23 a). A horizon that crosses the
year end is one Budget Cycle per fiscal year sharing the as-of date (Q24 a),
so a round's coverage is a question across Cycles: does every fiscal year the
horizon reaches have an APPROVED Cycle of the round? The horizon is counted in
DECLARED Regular periods (BUD-10-1), from ``fiscal_calendar.fiscal_period_rows``
(the EPM Fiscal Year calendar), never by adding calendar months: a period the
calendar does not declare is reported as undeclared, so the horizon is never
silently shorter than the Cycle says.

``rounds(cycles, period_rows)`` returns one row per round, ordered by
(scenario, as-of), each with a ``state`` and a ``sentence``:

- ``ok``: every period of the horizon has an approved Cycle for its year;
- ``uncovered``: some period has none. The sentence names the uncovered
  periods, the round's draft Cycles (approve them), the years with no Cycle
  at all (create one), and the periods past the declared calendar;
- ``undeclared_as_of``: the as-of date falls in no declared Regular period
  (only a Regular period holds a date, the deal_period_holds rule), so the
  horizon cannot be counted.

Declared sub-rules (not HMDs; say so in the PR): the round's horizon is the
longest its Cycles declare, and a disagreement between them is named in the
sentence; a cancelled Cycle is not part of any round and covers nothing; a
Cycle of a non-forecast scenario has no round (QB8 a) and is skipped.

Pure: imports nothing from frappe. The caller (the Forecast Horizon Coverage
report, S6 KA9) passes Budget Cycle rows with ``name, scenario_id,
scenario_type, fiscal_year, as_of_date, horizon_periods, docstatus`` and the
calendar rows with ``fiscal_year, fiscal_period, period_type, start_date,
end_date``.
"""
from datetime import date, datetime

from konsol.budget_bulk_model import FORECAST, as_of_key

OK = "ok"
UNCOVERED = "uncovered"
UNDECLARED_AS_OF = "undeclared_as_of"
STATES = (OK, UNCOVERED, UNDECLARED_AS_OF)

REGULAR = "Regular"
_LIVE = (0, 1)


def rounds(cycles, period_rows):
    """One row per forecast round, ordered by (scenario_id, as_of_date)."""
    regular = _regular_periods(period_rows)
    grouped = {}
    for c in cycles:
        if c.get("scenario_type") not in (None, FORECAST):
            continue
        if int(c.get("docstatus") or 0) not in _LIVE:
            continue
        key = as_of_key(c.get("as_of_date"))
        if not key:
            continue  # not a round (validate refuses it on a forecast Cycle)
        grouped.setdefault((c["scenario_id"], key), []).append(c)
    return [_round(scenario_id, as_of, members, regular) for (scenario_id, as_of), members in sorted(grouped.items())]


def _regular_periods(period_rows):
    """[(fy, fp, start_date, end_date)] of the declared Regular periods, in
    (fy, fp) order; a period declared on several rows is one, spanning them."""
    spans = {}
    for row in period_rows:
        if row.get("period_type") != REGULAR:
            continue
        key = (int(row["fiscal_year"]), int(row["fiscal_period"]))
        start, end = _as_date(row.get("start_date")), _as_date(row.get("end_date"))
        if start is None or end is None:
            continue
        if key in spans:
            spans[key] = (min(spans[key][0], start), max(spans[key][1], end))
        else:
            spans[key] = (start, end)
    return [(fy, fp, s, e) for (fy, fp), (s, e) in sorted(spans.items())]


def _as_date(value):
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value).strip()[:10])


def _round(scenario_id, as_of, members, regular):
    members = sorted(members, key=lambda c: (int(c.get("fiscal_year") or 0), c["name"]))
    names = [c["name"] for c in members]
    horizons = {c["name"]: int(c.get("horizon_periods") or 0) for c in members}
    horizon = max(horizons.values()) if horizons else 0
    approved_years = sorted({int(c["fiscal_year"]) for c in members if int(c.get("docstatus") or 0) == 1})
    draft_cycles = [c["name"] for c in members if int(c.get("docstatus") or 0) == 0]
    as_of_date = date.fromisoformat(as_of)
    row = {
        "scenario_id": scenario_id,
        "as_of_date": as_of,
        "as_of_period": None,
        "horizon_periods": horizon,
        "cycles": names,
        "approved_years": approved_years,
        "draft_cycles": draft_cycles,
        "periods": [],
        "uncovered": [],
        "undeclared_periods": 0,
        "state": None,
        "sentence": "",
    }
    head = f"Round {scenario_id} as of {as_of}"
    notes = []
    if len(set(horizons.values())) > 1:
        notes.append("its Cycles declare different horizons ("
                     + ", ".join(f"{n}: {h}" for n, h in horizons.items()) + "); the longest is counted")

    index = next((i for i, (_, _, s, e) in enumerate(regular) if s <= as_of_date <= e), None)
    if index is None:
        row["state"] = UNDECLARED_AS_OF
        row["sentence"] = (f"{head}: the as-of date is in no declared Regular period, so its horizon of "
                           f"{_periods(horizon)} cannot be counted. Declare the period in EPM Fiscal Year.")
        return _with_notes(row, notes)

    row["as_of_period"] = regular[index][:2]
    after = [(fy, fp) for fy, fp, _, _ in regular[index + 1:index + 1 + horizon]]
    row["periods"] = after
    row["undeclared_periods"] = horizon - len(after)
    row["uncovered"] = [(fy, fp) for fy, fp in after if fy not in approved_years]

    span = f"{head} covers {period_range_text(after)} ({_periods(horizon)})" if after else f"{head} ({_periods(horizon)})"
    if not row["uncovered"] and not row["undeclared_periods"]:
        row["state"] = OK
        years = ", ".join(f"FY{y}" for y in approved_years)
        row["sentence"] = f"{span}: approved for {years} ({', '.join(names)})."
        return _with_notes(row, notes)

    row["state"] = UNCOVERED
    parts = []
    if row["uncovered"]:
        parts.append(f"uncovered {period_range_text(row['uncovered'])}")
        missing_years = sorted({fy for fy, _ in row["uncovered"]})
        draft_by_year = {int(c["fiscal_year"]): c["name"] for c in members if c["name"] in draft_cycles}
        for fy in missing_years:
            if fy in draft_by_year:
                parts.append(f"{draft_by_year[fy]} is a draft: approve it to cover FY{fy}")
            else:
                parts.append(f"no Cycle of the round for FY{fy}: create one with the same as-of date")
    if row["undeclared_periods"]:
        last = regular[-1][:2]
        parts.append(f"{row['undeclared_periods']} of its {_periods(horizon)} are after the last declared Regular "
                     f"period (FY{last[0]} P{last[1]}): declare them in EPM Fiscal Year")
    if approved_years:
        parts.append("approved for " + ", ".join(f"FY{y}" for y in approved_years))
    row["sentence"] = f"{span}: " + "; ".join(parts) + "."
    return _with_notes(row, notes)


def _periods(n):
    """'1 period', '12 periods' (konsol#559: the sentence said '1 periods')."""
    return f"{n} period" if n == 1 else f"{n} periods"


def _with_notes(row, notes):
    if notes:
        row["sentence"] = row["sentence"].rstrip(".") + ". Note: " + "; ".join(notes) + "."
    return row


def period_range_text(periods):
    """'FY2027 P11-P12, FY2028 P1-P3': contiguous runs within a year compressed,
    years listed in order. '' for no periods."""
    runs = []
    for fy, fp in periods:
        if runs and runs[-1][0] == fy and runs[-1][2] == fp - 1:
            runs[-1][2] = fp
        else:
            runs.append([fy, fp, fp])
    out = []
    for fy, first, last in runs:
        out.append(f"FY{fy} P{first}" if first == last else f"FY{fy} P{first}-P{last}")
    return ", ".join(out)

"""Rate Sheet: the pure rules (konsol#592, pair 1).

Decided by Deepak Pai on 22 Nov 2026 (#592, #585):
- D1: a Statutory sheet per period, a Constant sheet per fiscal year.
- QA-1: a Constant sheet holds ONE rate per pair, used for Average and Closing.
- P1-Q4 A: the fiscal year and period are entered; the validity dates are
  derived from the calendar, so they cannot drift from it.
- #312-3: a correction supersedes the approved sheet (no cancel-then-amend);
  there is no Disabled flag (P1-Q3).

This module decides the window a sheet covers, which rows are valid, which
warehouse rows an approved sheet publishes, whether a rate moved too far, and
what a duplicate carries. ``rate_sheet.py`` reads the calendar and the sheets
and writes; the rules live here. It imports nothing from frappe or konsol.

A sheet is a mapping: ``rate_basis`` ("Statutory" or "Constant"),
``fiscal_year``, ``fiscal_period`` (None on a Constant sheet), ``rows``,
``name``, ``docstatus``. A calendar row is a mapping: ``fiscal_year``,
``period``, ``type``, ``start_date``, ``end_date``.

Closing periods (the choice): the warehouse's ``rate_period_map()``
(konsolidat, governed_rates.sql) reads a Closing-type period at the LAST
Regular period of its year, so a Closing period has no rates of its own.
``rate_period`` is that map, for every konsol reader of a period's rates (the
close gate, approved_quotes, the grid). A Constant sheet publishes every
declared period except a Closing one, and publishes a Closing period only in a
year that declares no Regular period (the map leaves it pointing at itself).
Opening and Adjustment periods map to themselves: they are published, and a
Statutory sheet may hold an Opening, Regular or Adjustment period's rates
(PR #595 review 1). A Statutory sheet for a Closing period is refused.
"""
import decimal

STATUTORY = "Statutory"
CONSTANT = "Constant"
BASES = (STATUTORY, CONSTANT)

OPENING = "Opening"
REGULAR = "Regular"
CLOSING = "Closing"
ADJUSTMENT = "Adjustment"
STATUTORY_PERIOD_TYPES = (OPENING, REGULAR, ADJUSTMENT)

QUOTED_PER = (1, 10, 100, 1000, 10000)
RATE_TYPES = ("Average", "Closing")

#: Keep identical to close_policy_model.RATE_MOVE_MESSAGE (a test pins it).
RATE_MOVE_MESSAGE = (
    "Declare the rate move threshold in Close Settings (the Close Lead or "
    "System Manager): a group rate that moves more than it from the previous "
    "approved rate or the ERP quote needs a Reason for Change."
)


def true_rate(quote, quoted_per):
    """Units of the group currency per 1 unit of the from-currency.

    Divided in decimal, as ``group_rates.true_rate`` does: Quoted Per is a
    power of ten, so the quotient is exact and the float is the nearest
    double to it (0.6607 per 100 is 0.006607)."""
    return float(decimal.Decimal(str(quote or 0)) / int(quoted_per or 1))


def _blank(value):
    return value is None or value == ""


def _pair(row):
    return f"{row.get('from_currency')} → {row.get('to_currency')}"


def _year_rows(calendar_rows, fiscal_year):
    return [r for r in calendar_rows or () if int(r["fiscal_year"]) == int(fiscal_year)]


# -- T1: the window ------------------------------------------------------------------

def window(sheet, calendar_rows):
    """((valid_from, valid_to), None), or (None, why).

    Statutory: the declared period's own start and end; the period must exist
    and be of type Opening, Regular or Adjustment (Closing is read at the
    year's last Regular period by the warehouse: ``rate_period``).
    Constant: no period; the year's first start to its last end."""
    basis = sheet.get("rate_basis")
    if basis not in BASES:
        return None, f"Rate Basis must be Statutory or Constant, not {basis!r}."
    year = sheet.get("fiscal_year")
    if _blank(year):
        return None, "A Rate Sheet needs a Fiscal Year."
    declared = _year_rows(calendar_rows, year)
    if not declared:
        return None, f"FY{int(year)} is not declared: create it in EPM Fiscal Year."
    period = sheet.get("fiscal_period")
    if basis == CONSTANT:
        if not _blank(period):
            return None, "A Constant sheet covers the whole year and has no Fiscal Period."
        return (min(r["start_date"] for r in declared), max(r["end_date"] for r in declared)), None
    if _blank(period):
        return None, "A Statutory sheet needs a Fiscal Period."
    match = [r for r in declared if int(r["period"]) == int(period)]
    if not match:
        return None, f"FY{int(year)} has no period {int(period)}: declare it in EPM Fiscal Year."
    types = {r["type"] for r in match}
    if not types <= set(STATUTORY_PERIOD_TYPES):
        bad = sorted(types - set(STATUTORY_PERIOD_TYPES))[0]
        extra = (" Its rates are those of the year's last Regular period." if bad == CLOSING else "")
        return None, (f"FY{int(year)} P{int(period)} is a {bad} period: a Statutory sheet needs an "
                      f"Opening, Regular or Adjustment period.{extra}")
    return (min(r["start_date"] for r in match), max(r["end_date"] for r in match)), None


def rate_period(calendar_rows, fiscal_year, fiscal_period):
    """The period whose rates ``(fiscal_year, fiscal_period)`` is translated
    at, as the warehouse's ``rate_period_map()`` reads it: a Closing-type
    period reads the LAST Regular period of its year; every other period, a
    Closing period in a year with no Regular period and an undeclared period
    read themselves. Only rows of ``fiscal_year`` are read."""
    fp = int(fiscal_period)
    declared = _year_rows(calendar_rows, fiscal_year)
    closing = any(int(r["period"]) == fp and r["type"] == CLOSING for r in declared)
    regular = [int(r["period"]) for r in declared if r["type"] == REGULAR]
    return max(regular) if closing and regular else fp


# -- T2: the rows ----------------------------------------------------------------------

def _per_ok(value):
    try:
        if isinstance(value, float) and value != int(value):
            return False
        return int(value) in QUOTED_PER
    except (TypeError, ValueError):
        return False


def _positive(value):
    try:
        return float(value) > 0
    except (TypeError, ValueError):
        return False


def row_problems(sheet):
    """Every reason the sheet's rows are invalid, one string each (empty if none)."""
    basis = sheet.get("rate_basis")
    rows = list(sheet.get("rows") or ())
    if not rows:
        return ["The sheet has no rates: add at least one currency pair."]
    problems, seen = [], set()
    for row in rows:
        label = _pair(row)
        key = (row.get("from_currency"), row.get("to_currency"))
        if row.get("from_currency") and row.get("from_currency") == row.get("to_currency"):
            problems.append(f"{label}: From and To are the same currency.")
        if key in seen:
            problems.append(f"{label}: this pair appears more than once on the sheet.")
        seen.add(key)
        if not _per_ok(row.get("quoted_per")):
            problems.append(f"{label}: Quoted Per must be one of "
                            f"{', '.join(str(p) for p in QUOTED_PER)}, not {row.get('quoted_per')!r}.")
        if basis == STATUTORY:
            if not _positive(row.get("average_quote")):
                problems.append(f"{label}: a Statutory rate needs an Average Quote above zero.")
            if not _positive(row.get("closing_quote")):
                problems.append(f"{label}: a Statutory rate needs a Closing Quote above zero.")
        elif basis == CONSTANT:
            if not _positive(row.get("rate")):
                problems.append(f"{label}: a Constant rate needs a Rate above zero.")
    return problems


# -- T3: what an approved sheet publishes -----------------------------------------------

def _published_periods(sheet, calendar_rows):
    """The fiscal periods a sheet publishes at (see the module note on Closing)."""
    if sheet["rate_basis"] == STATUTORY:
        return [int(sheet["fiscal_period"])]
    declared = _year_rows(calendar_rows, sheet["fiscal_year"])
    has_regular = any(r["type"] == REGULAR for r in declared)
    return sorted({int(r["period"]) for r in declared if not (r["type"] == CLOSING and has_regular)})


def published_rows(sheet, calendar_rows, actuals_code, constant_code="CONSTANT"):
    """epm_staging.group_exchange_rates rows for an APPROVED sheet, in the shape
    ``group_rates.publish_rates`` writes: [to, from, fiscal year, fiscal
    period, rate type, TRUE rate, document, rate set].

    Statutory: Average and Closing per pair at (year, period) under
    ``actuals_code``. Constant: Average and Closing, both at rate/per, for every
    published period of the year under ``constant_code``. Raises ValueError
    for a sheet whose window or rows are invalid: nothing is published
    from a sheet the rules refuse."""
    _, why = window(sheet, calendar_rows)
    if why:
        raise ValueError(why)
    problems = row_problems(sheet)
    if problems:
        raise ValueError(" ".join(problems))
    year, name = int(sheet["fiscal_year"]), sheet["name"]
    out = []
    if sheet["rate_basis"] == STATUTORY:
        period = int(sheet["fiscal_period"])
        for r in sheet["rows"]:
            per = r["quoted_per"]
            for rate_type, field in (("Average", "average_quote"), ("Closing", "closing_quote")):
                out.append([r["to_currency"], r["from_currency"], year, period, rate_type,
                            true_rate(r[field], per), name, actuals_code])
        return out
    for period in _published_periods(sheet, calendar_rows):
        for r in sheet["rows"]:
            rate = true_rate(r["rate"], r["quoted_per"])
            for rate_type in RATE_TYPES:
                out.append([r["to_currency"], r["from_currency"], year, period, rate_type,
                            rate, name, constant_code])
    return out


# -- T4: the move check -------------------------------------------------------------------

def _rate_fields(sheet):
    if sheet["rate_basis"] == STATUTORY:
        return (("Average", "average_quote", "erp_average_quote"),
                ("Closing", "closing_quote", "erp_closing_quote"))
    return ((None, "rate", None),)


def _label(row, rate_type):
    return f"{_pair(row)} {rate_type}" if rate_type else _pair(row)


def move_problems(sheet, previous_sheet, threshold):
    """Reasons the sheet needs a Reason for Change because a rate moves too far;
    empty when none. The semantics of the group rates' move check
    (``group_rates.move_problem``), per row.

    Each rate (true rate, so Quoted Per does not matter) is compared with the
    same pair and type on ``previous_sheet`` (the previous approved sheet of
    the same basis, or None) and with the row's ERP quote it was proposed from.
    A move OVER ``threshold`` (a fraction, 0.5 for 50%) is a problem; exactly at
    it is not. A pair with nothing to compare against needs no reason. With
    something to compare and no declared threshold, the one answer is
    ``RATE_MOVE_MESSAGE``: the threshold is never guessed."""
    if previous_sheet is not None and previous_sheet["rate_basis"] != sheet["rate_basis"]:
        raise ValueError(f"Rate Basis differs: {previous_sheet['name']} is {previous_sheet['rate_basis']}, "
                         f"this sheet is {sheet['rate_basis']}.")
    prev_rows = {}
    for r in (previous_sheet or {}).get("rows") or ():
        prev_rows[(r["from_currency"], r["to_currency"])] = r
    problems, undeclared = [], False
    for row in sheet.get("rows") or ():
        old = prev_rows.get((row["from_currency"], row["to_currency"]))
        for rate_type, field, erp_field in _rate_fields(sheet):
            if _blank(row.get(field)):
                continue
            rate = true_rate(row[field], row.get("quoted_per"))
            refs = []
            if old is not None and not _blank(old.get(field)):
                refs.append((true_rate(old[field], old.get("quoted_per")),
                             f"the previous approved rate {previous_sheet['name']}"))
            if erp_field and not _blank(row.get(erp_field)):
                refs.append((true_rate(row[erp_field], row.get("quoted_per")), "the ERP quote"))
            refs = [(ref, what) for ref, what in refs if ref > 0]
            if not refs:
                continue
            if threshold is None:
                undeclared = True
                continue
            moves = [f"{rate / ref - 1.0:+.0%} from {what} ({ref:.9g})"
                     for ref, what in refs if abs(rate / ref - 1.0) > threshold]
            if moves:
                problems.append(f"{_label(row, rate_type)}: this rate ({rate:.9g}) moves "
                                + " and ".join(moves)
                                + f". A move over {threshold * 100:g}% can be real, but say why "
                                  "(Reason for Change) before it is saved.")
    return [RATE_MOVE_MESSAGE] if undeclared else problems


def key_label(basis, fiscal_year, fiscal_period):
    """The sheet key as a person reads it: "FY2025 P6", or "FY2025 Constant"."""
    if basis == CONSTANT:
        return f"FY{int(fiscal_year)} Constant"
    return f"FY{int(fiscal_year)} P{int(fiscal_period)}"


def plan_duplicate(previous_sheet, target_key, existing_keys):
    """(plan, None), or (None, why): a new draft for ``target_key`` carrying the
    rows of ``previous_sheet`` (replaces ``rate_copy_model.plan_copy``).

    ``target_key`` is (rate_basis, fiscal_year, fiscal_period), the period None
    for a Constant sheet. ``existing_keys`` is the set of keys that already have
    a draft or an approved sheet: a target in it is refused (a correction
    supersedes, it is not a duplicate). Only an approved sheet (docstatus 1) is a
    source. Rows are copied as Duplicated, quotes and Quoted Per only: ERP
    quotes, labels and legacy names stay behind. The plan holds no name; the
    controller names the sheet. Nothing is submitted."""
    try:
        basis, year, period = target_key
    except (TypeError, ValueError):
        return None, "The target key must be (Rate Basis, Fiscal Year, Fiscal Period)."
    if basis not in BASES:
        return None, f"Rate Basis must be Statutory or Constant, not {basis!r}."
    if int(previous_sheet.get("docstatus", 0)) != 1:
        return None, f"{previous_sheet.get('name')} is not approved: only an approved sheet can be duplicated."
    if previous_sheet["rate_basis"] != basis:
        return None, (f"Rate Basis differs: {previous_sheet['name']} is {previous_sheet['rate_basis']}, "
                      f"the target is {basis}.")
    if basis == STATUTORY and _blank(period):
        return None, "A Statutory sheet needs a Fiscal Period."
    if basis == CONSTANT and not _blank(period):
        return None, "A Constant sheet covers the whole year and has no Fiscal Period."
    key = (basis, int(year), None if basis == CONSTANT else int(period))
    label = key_label(*key)
    source_key = (previous_sheet["rate_basis"], int(previous_sheet["fiscal_year"]),
                  None if basis == CONSTANT else int(previous_sheet["fiscal_period"]))
    if key == source_key:
        return None, f"{label} is {previous_sheet['name']} itself: duplicate into another {'year' if basis == CONSTANT else 'period'}."
    if key in {(b, int(y), None if p is None else int(p)) for b, y, p in existing_keys}:
        return None, (f"{label} already has a sheet (draft or approved). To correct an approved sheet, "
                      "supersede it instead of duplicating.")
    fields = (("average_quote", "closing_quote") if basis == STATUTORY else ("rate",))
    rows = [{"from_currency": r["from_currency"], "to_currency": r["to_currency"],
             "quoted_per": r["quoted_per"], **{f: r.get(f) for f in fields}, "source": "Duplicated"}
            for r in previous_sheet.get("rows") or ()]
    src = key_label(*source_key)
    note = (f"Duplicated from {previous_sheet['name']}, the approved {basis} sheet for {src}. "
            "Approve it for this key only if the rates still hold.")
    return {"rate_basis": basis, "fiscal_year": key[1], "fiscal_period": key[2], "rows": rows,
            "copied_from": previous_sheet["name"], "source_note": note}, None

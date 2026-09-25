"""TB view model: this period against the previous period, by account (konsol#305 A12, story 3.4).

Pure: imports no frappe, and is loaded by path in its tests. The input rows are
``parse_tb_csv`` output (trial_balance_submission.py). A line is keyed by
``(main_account, partner_data_area_id)``; its amount is ``debit - credit``, and
a line with a partner is intercompany.

``previous_period`` picks the previous declared Regular period of the fiscal
calendar, across a year boundary: P01's previous is last year's P12, never an
Opening, Closing or Adjustment period.

``compare`` never compares against zero. When there is no previous trial
balance, every ``previous`` and ``change`` is None and ``previous_note`` says
so. When the two trial balances declare different amount bases, the amounts
are shown but every ``change`` is None and ``basis_note`` says why.
"""

import importlib.util
import os

_APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load_sibling(name, filename):
    spec = importlib.util.spec_from_file_location(name, os.path.join(_APP_DIR, filename))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_basis = _load_sibling("konsol_close_tb_view_basis_model", "tb_basis_model.py")

PARTNER = "partner_data_area_id"
REGULAR = "Regular"
NO_PREVIOUS_PERIOD = "No previous period is declared in the fiscal calendar"


def _check_basis(basis):
    if basis not in _basis.AMOUNT_BASES:
        raise ValueError(
            "Unknown amount basis %r: expected one of %s"
            % (basis, ", ".join(_basis.AMOUNT_BASES)))


def _net_by_key(rows):
    """{(account, partner): debit - credit}; repeated keys are summed."""
    out = {}
    for row in rows:
        key = (row["main_account"], row.get(PARTNER) or "")
        out[key] = out.get(key, 0.0) + (row.get("debit") or 0.0) - (row.get("credit") or 0.0)
    return out


def compare(current_rows, previous_rows, current_basis, previous_basis, previous_code):
    """Join this period's TB with the previous period's, by (account, partner).

    ``previous_rows`` is None when no trial balance was loaded for the previous
    period (``[]`` is a loaded TB with no lines). ``previous_code`` is None when
    the calendar declares no previous period. ``previous_basis`` is ignored
    when ``previous_rows`` is None.

    Returns ``{rows, basis_note, previous_note, previous_code}``; each row is
    ``{account, partner, is_ic, current, previous, change}``, sorted by account
    then partner.
    """
    _check_basis(current_basis)
    has_previous = previous_rows is not None
    if has_previous:
        _check_basis(previous_basis)

    previous_note = None
    if previous_code is None:
        previous_note = NO_PREVIOUS_PERIOD
    elif not has_previous:
        previous_note = "No trial balance for %s" % previous_code

    basis_note = None
    comparable = has_previous
    if has_previous and current_basis != previous_basis:
        comparable = False
        basis_note = (
            "This period is %s and %s is %s: the change is not comparable."
            % (current_basis, previous_code, previous_basis))

    cur = _net_by_key(current_rows)
    prev = _net_by_key(previous_rows) if has_previous else {}

    rows = []
    for key in sorted(set(cur) | set(prev)):
        account, partner = key
        c = cur.get(key)
        p = prev.get(key)
        change = c - p if comparable and c is not None and p is not None else None
        rows.append({
            "account": account, "partner": partner, "is_ic": bool(partner),
            "current": c, "previous": p, "change": change,
        })
    return {"rows": rows, "basis_note": basis_note,
            "previous_note": previous_note, "previous_code": previous_code}


def previous_period(period_rows, fiscal_year, fiscal_period):
    """The previous declared Regular period row, or None for the first one.

    ``period_rows`` are ``fiscal_calendar.fiscal_period_rows()``; their order is
    not relied on. Raises ValueError when the period is not declared or is not
    Regular.
    """
    key = (fiscal_year, fiscal_period)
    current = [r for r in period_rows if (r["fiscal_year"], r["fiscal_period"]) == key]
    if not current:
        raise ValueError(
            "FY%s P%02d is not declared in the fiscal calendar" % (fiscal_year, fiscal_period))
    if current[0].get("period_type") != REGULAR:
        raise ValueError(
            "FY%s P%02d is a %s period: only Regular periods are compared"
            % (fiscal_year, fiscal_period, current[0].get("period_type")))
    earlier = [r for r in period_rows
               if r.get("period_type") == REGULAR
               and (r["fiscal_year"], r["fiscal_period"]) < key]
    if not earlier:
        return None
    return max(earlier, key=lambda r: (r["fiscal_year"], r["fiscal_period"]))

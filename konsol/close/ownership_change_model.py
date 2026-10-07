"""An ownership change: its refusals and its structural effect, pure
(konsol#305 O51; story 4.2).

Decisions:
- #305-4.2-1 (6 Oct): the effect is structural. It is pct and method before
  and after, the periods covered, and the later signed periods that will need
  re-signing (W4-4). Goodwill, NCI and results are never previewed. Deals
  (acquisition, disposal) stay in Desk.
- #305-Q1-1 (Deepak Pai, 7 Oct): approving a change end-dates its predecessor
  and a cancel restores it. A change between two existing periods is refused
  (refusal 8).
- The sentences are those of archive/konsol-305-d2/wireframe-4.2.md, confirmed
  as drawn by Deepak Pai on 7 Oct.

The preview, the save, the draft list and both approval views read this one
module (C-O4). It imports no frappe and no konsol: it loads ``period_name.py``
by path, so stub ``konsol.close`` packages in host tests never need it.

Inputs:
- ``change``: ``{entity, effective_date, ownership_pct, consolidation_method}``.
  ``entity`` is required, because every sentence names it. A missing key
  raises KeyError.
- ``current``: the node's submitted Ownership Period that covers
  ``effective_date`` (``name``, ``effective_date``, ``end_date``,
  ``ownership_pct``, ``consolidation_method``), or None.
- ``later_exists``: falsy, or the ``effective_date`` of the node's submitted
  period that starts after the change. The sentence names that date.
- ``pending_exists``: falsy, or the name of the node's draft awaiting approval.
- ``period_rows``: ``fiscal_calendar.fiscal_period_rows()``. Only Regular rows
  count as first days, so an Adjustment period's start is never a first day;
  ``effect``'s ``resign`` list reads every row (O54a).

Dates may be ``datetime.date`` objects or ISO strings. The output always uses
ISO strings.
"""
import datetime
import importlib.util
import math
import os

_HERE = os.path.dirname(os.path.abspath(__file__))


def _load_sibling(name):
    spec = importlib.util.spec_from_file_location(
        "konsol_close_ownership_change_model_" + name, os.path.join(_HERE, name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


period_name = _load_sibling("period_name").period_name

#: The Select options of ownership_period.json ``consolidation_method``. The
#: test pins them to the JSON.
METHODS = ("full", "proportional", "equity", "none")

NOT_SHOWN = "Goodwill, NCI and results are not previewed; they change at the next build."

PCT_SENTENCE = "Ownership % must be a number from 0 to 100."
METHOD_SENTENCE = "Method must be one of %s." % ", ".join(METHODS)


def _iso(value):
    """An ISO date string of a date or a string. A blank value gives None."""
    if value in (None, ""):
        return None
    if isinstance(value, (datetime.date, datetime.datetime)):
        return value.isoformat()[:10]
    return str(value)[:10]


def _pct(value):
    """The ownership % as a float in 0..100, or None if it is not one. A
    bool, NaN or infinity is not a number here."""
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(number) or math.isinf(number) or number < 0 or number > 100:
        return None
    return number


def _pct_text(number):
    """100.0 → "100", 80.5 → "80.5"."""
    number = float(number)
    return "%d" % number if number == int(number) else repr(number)


def _regular(period_rows):
    """The Regular rows, in calendar order."""
    rows = [r for r in period_rows if r.get("period_type") == "Regular"]
    return sorted(rows, key=lambda r: _iso(r["start_date"]))


def _name(row):
    return period_name(row["fiscal_year"], row["fiscal_period"])


def _containing(regular, date_iso):
    """The Regular row whose span contains ``date_iso``, or None."""
    for row in regular:
        if _iso(row["start_date"]) <= date_iso <= _iso(row["end_date"]):
            return row
    return None


def _next_after(regular, row):
    later = [r for r in regular if _iso(r["start_date"]) > _iso(row["start_date"])]
    return later[0] if later else None


def problems(change, current, later_exists, pending_exists, period_rows):
    """The refusals of ``change``, as sentences in the order the wireframe
    shows them. An empty list means it may be drafted. A check that depends on
    a value already refused (for example "nothing changes" with a bad %) is
    skipped rather than guessed."""
    entity = change["entity"]
    eff = _iso(change["effective_date"])
    pct = _pct(change["ownership_pct"])
    method = change["consolidation_method"]
    regular = _regular(period_rows)
    period = _containing(regular, eff) if eff else None
    out = []

    # 1. No current ownership (C-O3): a first holding is a Desk record.
    if current is None:
        out.append(
            "%s has no ownership for %s: record its first ownership in Desk "
            "(an acquisition is a Business Combination)."
            % (entity, _name(period) if period else eff))

    # 2. The first day of a Regular period (W2-P1, M13, C-O2).
    if period is None:
        out.append(
            "%s is in no declared Regular fiscal period: an ownership change "
            "must start on the first day of one." % eff)
    elif _iso(period["start_date"]) != eff:
        nxt = _next_after(regular, period)
        choices = []
        if nxt is not None:
            choices.append("%s, from %s" % (_name(nxt), _iso(nxt["start_date"])))
        choices.append("%s, from %s" % (_name(period), _iso(period["start_date"])))
        out.append(
            "Ownership changes take effect on the first day of a period: the "
            "warehouse reads ownership on each period's first day. %s is inside "
            "%s; choose %s." % (eff, _name(period), ", or ".join(choices)))

    # 3. The period must be Open.
    if period is not None and period.get("status") != "Open":
        out.append("%s is %s: an ownership change must start in an Open period."
                   % (_name(period), period.get("status")))

    # 4. and 5. The values.
    if pct is None:
        out.append(PCT_SENTENCE)
    if method not in METHODS:
        out.append(METHOD_SENTENCE)

    if current is not None:
        cur_from = _iso(current["effective_date"])
        # 6. Nothing changes.
        if (pct is not None and method in METHODS
                and pct == float(current["ownership_pct"])
                and method == current["consolidation_method"]):
            out.append("Nothing changes: %s is already %s %% %s from %s."
                       % (entity, _pct_text(pct), method, cur_from))
        # 7. The change must start after the current period's start.
        if eff and cur_from >= eff:
            out.append("The change must start after the current period's start (%s)."
                       % cur_from)

    # 8. A later period exists (#305-Q1-1: never between two periods).
    if later_exists:
        out.append("%s already has an ownership period from %s: change or cancel "
                   "that one first." % (entity, _iso(later_exists)))

    # 9. A change is already awaiting approval.
    if pending_exists:
        out.append("A change for %s is already awaiting approval (%s): edit that draft."
                   % (entity, pending_exists))
    return out


def _key(fy, fp):
    return (int(fy), int(fp))


def effect(change, current, period_rows, signed_keys):
    """The structural effect of ``change`` if it is approved. It carries no
    amount: ``not_shown`` says so.

    ``signed_keys`` holds the ``(fiscal_year, fiscal_period)`` of each period
    whose latest run is signed (``signoff_gate.latest_signed_runs()`` keys).

    Raises ValueError when the change has no current period, does not start
    on a Regular period's first day, or has a bad % or method. The effect of
    a refused change is never guessed. ``resign`` lists signed periods of
    every type (Regular, Opening, Closing, Adjustment) that start on or after
    the change (O54a); a signed key that is not in the calendar at all
    raises.
    """
    if current is None:
        raise ValueError("an ownership change has no effect without a current period")
    eff = _iso(change["effective_date"])
    regular = _regular(period_rows)
    first = _containing(regular, eff) if eff else None
    if first is None or _iso(first["start_date"]) != eff:
        raise ValueError("%s is not the first day of a Regular period" % eff)
    pct = _pct(change["ownership_pct"])
    if pct is None:
        raise ValueError(PCT_SENTENCE)
    method = change["consolidation_method"]
    if method not in METHODS:
        raise ValueError(METHOD_SENTENCE)

    cur_to = _iso(current.get("end_date"))
    if cur_to is None:
        periods = "%s onward (open-ended)" % _name(first)
    else:
        last = _containing(regular, cur_to)
        if last is None:
            raise ValueError("the current period's end %s is in no Regular period" % cur_to)
        periods = "%s to %s" % (_name(first), _name(last))

    # O54a: every signed period from the first affected one onward needs
    # re-signing, whatever its type (Regular, Opening, Closing, Adjustment).
    # Only the first-day checks above are Regular-only. A signed key absent
    # from the calendar is corrupt data and raises.
    by_key = {_key(r["fiscal_year"], r["fiscal_period"]): r for r in period_rows}
    resign_rows = []
    for fy, fp in signed_keys:
        row = by_key.get(_key(fy, fp))
        if row is None:
            raise ValueError("signed period %r is not in the fiscal calendar"
                             % ((fy, fp),))
        if _iso(row["start_date"]) >= eff:
            resign_rows.append(row)
    # Calendar order: by start date, then year and period, so an Opening P00
    # that shares its start with P01 comes first.
    resign_rows.sort(key=lambda r: (_iso(r["start_date"]), int(r["fiscal_year"]),
                                    int(r["fiscal_period"])))

    current_ends = (datetime.date.fromisoformat(eff) - datetime.timedelta(days=1)).isoformat()
    return {
        "before": {"pct": float(current["ownership_pct"]),
                   "method": current["consolidation_method"],
                   "from": _iso(current["effective_date"]), "to": cur_to},
        "after": {"pct": pct, "method": method, "from": eff, "to": cur_to},
        "current_ends": current_ends,
        "first_period": _name(first),
        "periods": periods,
        "resign": [_name(r) for r in resign_rows],
        "not_shown": NOT_SHOWN,
    }

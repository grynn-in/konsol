"""Persona and landing period for the close app, pure (konsol#305 A03).

Implements D5 of konsol#298:

- The Close Lead, Group Accountant and Entity Accountant land on the oldest
  *ended* Regular period that is still Open; if there is none, on the Regular
  period containing today.
- The Viewer lands on the latest signed-off period, with a one-click switch
  to the provisional period (the landing above).
- "Last viewed" memory and the calendar month are rejected.

When nothing qualifies the result carries ``period: None`` and a reason
naming the fix; it never guesses. Imports nothing from frappe or konsol.
"""
import datetime

CLOSE_LEAD = "close_lead"
GROUP_ACCOUNTANT = "group_accountant"
ENTITY_ACCOUNTANT = "entity_accountant"
VIEWER = "viewer"

#: (roles, persona) in priority order.
PERSONA_PRIORITY = (
    (("EPM Admin", "System Manager"), CLOSE_LEAD),
    (("EPM Analyst",), GROUP_ACCOUNTANT),
    (("Entity Accountant",), ENTITY_ACCOUNTANT),
    (("EPM User",), VIEWER),
)

PERSONAS = tuple(p for _, p in PERSONA_PRIORITY)

NO_SIGNED_REASON = (
    "No period has been signed off yet. Switch to the provisional period "
    "to see the close in progress."
)


def persona(roles):
    """The close persona for ``roles``, or None when no close role is held."""
    held = set(roles or ())
    for candidates, name in PERSONA_PRIORITY:
        if held.intersection(candidates):
            return name
    return None


def _date(value):
    if isinstance(value, datetime.datetime):
        return value.date()
    if isinstance(value, datetime.date):
        return value
    if isinstance(value, str) and value:
        return datetime.date.fromisoformat(value[:10])
    return None


def _key(row):
    return (int(row["fiscal_year"]), int(row["fiscal_period"]))


def _regular(rows):
    for row in rows or ():
        if row.get("period_type") != "Regular":
            continue
        start, end = _date(row.get("start_date")), _date(row.get("end_date"))
        if start is None or end is None:
            continue
        yield row, start, end


FIRST_CLOSE_UNDECLARED = (
    "Declare the first close period (Close Settings, Close Order). konsol does not "
    "guess where closing starts: every earlier period is history (konsol#303)."
)


def _working_landing(rows, today, first_close):
    if first_close is None:
        return {"period": None, "rule": "first_close_undeclared", "reason": FIRST_CLOSE_UNDECLARED}
    first = (int(first_close[0]), int(first_close[1]))
    candidates = [(row, start, end) for row, start, end in _regular(rows) if _key(row) >= first]
    open_ended = [
        _key(row) for row, _start, end in candidates
        if row.get("status") == "Open" and end < today
    ]
    if open_ended:
        return {"period": min(open_ended), "rule": "oldest_open_ended", "reason": None}
    for row, start, end in candidates:
        if start <= today <= end:
            return {"period": _key(row), "rule": "contains_today", "reason": None}
    return {
        "period": None,
        "rule": None,
        "reason": (
            "No Regular period is Open and ended, and none contains today "
            "(%s). Declare the periods in EPM Fiscal Year." % today.isoformat()
        ),
    }


def landing(rows, signed_keys, persona, today, first_close):
    """The period a persona lands on.

    ``rows`` are ``fiscal_calendar.fiscal_period_rows()`` dicts (``status`` is
    the effective status); ``signed_keys`` is the set of
    ``(fiscal_year, fiscal_period)`` with a valid sign-off; ``today`` is a
    ``datetime.date``. ``first_close`` is the declared first close period
    ``(fiscal_year, fiscal_period)`` or None; earlier periods are history and
    never landed on, and None is a named gap (A03b, konsol#303). It has no
    default on purpose.

    Returns ``{"period", "rule", "reason"}``; a Viewer's result also carries
    ``provisional``, the landing a non-viewer would get.
    """
    if persona not in PERSONAS:
        raise ValueError("Unknown close persona: %r" % (persona,))
    working = _working_landing(rows, today, first_close)
    if persona != VIEWER:
        return working
    signed = [(int(fy), int(fp)) for fy, fp in (signed_keys or ())]
    if signed:
        return {"period": max(signed), "rule": "latest_signed", "reason": None,
                "provisional": working}
    return {"period": None, "rule": None, "reason": NO_SIGNED_REASON,
            "provisional": working}


# --- A04: period states, other open periods, the catch-up label ------------

#: Copied from assertion_run.SIGNED_STATES (assertion_run.py:224); not imported.
SIGNED_STATES = ("Signed Off", "Acknowledged", "Overridden")

NOT_RUN = "Not run"
NOT_SIGNED_OFF = "Not signed off"


def _first(first_close):
    if first_close is None:
        return None
    return (int(first_close[0]), int(first_close[1]))


def _period_name(key):
    return "FY%d P%02d" % key


def _catch_up(regular_keys, first, loaded):
    """The catch-up label for the first close period, or None (#303 point 1).

    The label appears only when earlier Regular periods of its year exist and
    the period right after the previous loaded one is not the first close
    period itself. With nothing loaded before, the catch-up covers from the
    first Regular period of its year.
    """
    earlier_in_year = [k for k in regular_keys if k[0] == first[0] and k < first]
    if not earlier_in_year:
        return None
    before = [k for k in loaded if k < first]
    if before:
        previous = max(before)
        after = [k for k in regular_keys if k > previous]
        start = min(after) if after else first
    else:
        start = min(earlier_in_year)
    if start >= first:
        return None
    return "Catch-up: covers from %s" % _period_name(start)


def period_states(rows, runs, first_close, loaded_keys):
    """One state per Regular period, oldest first, plus configuration gaps.

    ``rows`` are ``fiscal_period_rows()`` dicts; ``runs`` maps a key to the
    latest terminal run ``{"name", "status", "signoff_status"}``;
    ``first_close`` is the declared first close key or None (no default);
    ``loaded_keys`` are the keys with a submitted TB.

    Returns ``{"states": [...], "config_gaps": [...]}``. With no first close
    period, ``is_history`` and ``catch_up`` are None on every state and the
    gap ``first_close_undeclared`` is returned: nothing is assumed.
    """
    runs = runs or {}
    loaded = {(int(fy), int(fp)) for fy, fp in (loaded_keys or ())}
    first = _first(first_close)
    regular = sorted(((_key(row), row) for row, _s, _e in _regular(rows)), key=lambda kr: kr[0])
    regular_keys = [k for k, _row in regular]
    catch_up = _catch_up(regular_keys, first, loaded) if first is not None else None
    states = []
    for key, row in regular:
        run = runs.get(key) or {}
        signoff = run.get("signoff_status") or NOT_SIGNED_OFF
        states.append({
            "key": key,
            "code": row.get("period_code"),
            "label": row.get("period_label") or row.get("period_code"),
            "status": row.get("status"),
            "start_date": row.get("start_date"),
            "end_date": row.get("end_date"),
            "run": run.get("name"),
            "checks": run.get("status") or NOT_RUN,
            "signoff": signoff,
            "is_signed": signoff in SIGNED_STATES,
            "is_history": None if first is None else key < first,
            "catch_up": catch_up if key == first else None,
        })
    gaps = []
    if first is None:
        gaps.append({"code": "first_close_undeclared", "message": FIRST_CLOSE_UNDECLARED})
    return {"states": states, "config_gaps": gaps}


def other_open(states, selected_key, first_close):
    """Open periods from the first close period on, other than the selected one, oldest first.

    History (before ``first_close``) is never open work. With no first close
    period declared nothing is reported; the gap is surfaced by
    ``period_states``.
    """
    first = _first(first_close)
    if first is None:
        return []
    selected = None if selected_key is None else (int(selected_key[0]), int(selected_key[1]))
    found = [
        s for s in states or ()
        if s.get("status") == "Open" and tuple(s["key"]) >= first and tuple(s["key"]) != selected
    ]
    return sorted(found, key=lambda s: tuple(s["key"]))

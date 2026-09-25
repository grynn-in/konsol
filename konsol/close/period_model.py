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


def _working_landing(rows, today):
    open_ended = [
        _key(row) for row, _start, end in _regular(rows)
        if row.get("status") == "Open" and end < today
    ]
    if open_ended:
        return {"period": min(open_ended), "rule": "oldest_open_ended", "reason": None}
    for row, start, end in _regular(rows):
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


def landing(rows, signed_keys, persona, today):
    """The period a persona lands on.

    ``rows`` are ``fiscal_calendar.fiscal_period_rows()`` dicts (``status`` is
    the effective status); ``signed_keys`` is the set of
    ``(fiscal_year, fiscal_period)`` with a valid sign-off; ``today`` is a
    ``datetime.date``.

    Returns ``{"period", "rule", "reason"}``; a Viewer's result also carries
    ``provisional``, the landing a non-viewer would get.
    """
    if persona not in PERSONAS:
        raise ValueError("Unknown close persona: %r" % (persona,))
    working = _working_landing(rows, today)
    if persona != VIEWER:
        return working
    signed = [(int(fy), int(fp)) for fy, fp in (signed_keys or ())]
    if signed:
        return {"period": max(signed), "rule": "latest_signed", "reason": None,
                "provisional": working}
    return {"period": None, "rule": None, "reason": NO_SIGNED_REASON,
            "provisional": working}

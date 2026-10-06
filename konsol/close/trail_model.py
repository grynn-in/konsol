"""Close Event trail model, pure (konsol#305 W2, #298 story 10.1; amended 2
Oct by #305-W2-8, #305-W2-9).

Turns a period's Close Events into the board's summary (signed off by and
when, the result, closed, locked, and the exception counts) and the events
newest first.

- ``ordered(events)``: the events newest first, by ``(at, name)`` descending.
- ``visible(events, allowed)``: scopes entity events to the caller's allowed
  entities, mirroring ``period_grid_model``'s cut
  (period_grid_model.py:251-253; E2-6). ``allowed is None`` keeps
  everything. Otherwise an event is kept when its ``entity`` is blank
  (group-level: rates, journals, period, year and sign-off events) or is in
  ``allowed``. Returns ``(events, hidden)``.
- ``summary(events)``: the board's summary. Call it on an already-scoped
  list (``visible()``'s first element), never on the raw events, so a count
  cannot reveal a hidden entity's activity beyond the ``hidden`` number
  ``visible()`` already gives.

Story 10.2 (filter + CSV):

- ``parse_filters(kinds, actors, entities, date_from, date_to)``: the
  filters as a GET sends them (a JSON list string or a list; an ISO date)
  read into ``{"kinds", "actors", "entities", "date_from", "date_to"}``.
  A blank or empty list is no filter on that field (None). An unknown
  kind, a non-list, a non-string member, a bad date or a reversed range
  raises ValueError naming it -- never silently ignored.
- ``filtered(events, filters)``: the events every given filter matches
  (AND across fields, OR within one), in the input order. Call it on an
  already-scoped list: it only ever cuts, so naming a hidden entity gives
  nothing back. ``GROUP_LEVEL`` in ``entities`` selects the blank-entity
  (group-level) events. The date range is inclusive and compares the
  calendar day of ``at`` (the site's local time, as stored).
- ``options(events)``: the distinct kinds, actors and entities (blank as
  ``GROUP_LEVEL``), sorted -- the filter choices. Call it on the scoped
  list, so it never names a hidden entity or its actors.
- ``CSV_COLUMNS`` / ``csv_text(rows)``: the export. One row per event, in
  the given order, with exactly these columns:

  ``event`` (Close Event name), ``at`` (ISO, the site's UTC offset),
  ``fiscal_year``, ``fiscal_period`` (0 for a year event), ``kind`` (the
  stored kind), ``entity`` (blank for group-level), ``actor`` (user id),
  ``actor_name`` (full name, or the id when the user was deleted),
  ``actor_persona``, ``reference_doctype``, ``reference_name``,
  ``reason``, ``source`` (``live`` or ``backfill``), ``detail`` (the
  stored detail as JSON with sorted keys, blank when none).

  A text cell that starts with ``=``, ``+``, ``-``, ``@``, a tab or a
  carriage return is prefixed with ``'`` so a spreadsheet never runs it
  as a formula (declared, the one change to a stored value). A row
  missing a column raises KeyError rather than writing a blank.

An unknown ``kind`` raises ValueError naming it; it is never counted or
shown as another kind.

Imports only ``close_event_model`` (for ``KINDS``), loaded as a sibling
(mirrors fiscal_calendar.py:62-73 ``_load_sibling``). Imports no frappe.
"""
import csv
import datetime
import importlib.util
import io
import json
import os

_APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load_sibling(name, filename):
    spec = importlib.util.spec_from_file_location(name, os.path.join(_APP_DIR, filename))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


close_event_model = _load_sibling("konsol_close_trail_close_event_model", "close/close_event_model.py")

_CLOSED_KINDS = ("period_closed", "year_closed")
_LOCKED_KINDS = ("period_locked", "year_locked")
_REOPENED_KINDS = ("period_reopened", "year_reopened")


def ordered(events):
    """``events`` newest first, by ``(at, name)`` descending; a tie on
    ``at`` is broken by ``name`` descending too."""
    return sorted(events, key=lambda e: (e["at"], e["name"]), reverse=True)


def visible(events, allowed):
    """Scope ``events`` to ``allowed`` entities. ``allowed is None`` keeps
    everything, with ``hidden`` 0. Otherwise an event is kept when its
    ``entity`` is blank (group-level) or is in ``allowed``. Returns
    ``(kept_events, hidden_count)``."""
    if allowed is None:
        return list(events), 0
    kept = [e for e in events if not e.get("entity") or e.get("entity") in allowed]
    hidden = len(events) - len(kept)
    return kept, hidden


def _latest(events, kinds):
    candidates = [e for e in events if e["kind"] in kinds]
    if not candidates:
        return None
    return max(candidates, key=lambda e: e["at"])


def _span(events, close_kinds, reopen_kinds):
    """The latest of ``close_kinds``, as ``{"by", "at"}``, unless a later
    event in ``reopen_kinds`` follows it -- then None."""
    latest_close = _latest(events, close_kinds)
    if latest_close is None:
        return None
    latest_reopen = _latest(events, reopen_kinds)
    if latest_reopen is not None and latest_reopen["at"] > latest_close["at"]:
        return None
    return {"by": latest_close.get("actor"), "at": latest_close.get("at")}


def _signoff(events):
    latest_signed = _latest(events, ("signed_off",))
    if latest_signed is None:
        return {"state": "none"}
    latest_void = _latest(events, ("signoff_voided",))
    if latest_void is not None and latest_void["at"] > latest_signed["at"]:
        return {
            "state": "voided",
            "by": latest_void.get("actor"),
            "at": latest_void.get("at"),
            "reason": latest_void.get("reason"),
        }
    detail = latest_signed.get("detail") or {}
    return {
        "state": "signed",
        "by": latest_signed.get("actor"),
        "at": latest_signed.get("at"),
        "result": detail.get("signoff_status"),
        "run_status": detail.get("run_status"),
        "reason": latest_signed.get("reason"),
        "warnings": detail.get("warnings"),
    }


def summary(events):
    """The board's summary dict for ``events``: ``signoff``, ``closed``,
    ``locked`` and ``counts``. Raises ValueError naming an unknown
    ``kind``."""
    events = list(events)
    for event in events:
        if event["kind"] not in close_event_model.KINDS:
            raise ValueError("unknown Close Event kind %r." % (event["kind"],))

    counts = {
        "approvals": 0,
        "self_approvals": 0,
        "rejections": 0,
        "on_behalf_uploads": 0,
        "acknowledgements": 0,
        "overrides": 0,
        "reopenings": 0,
        "recovered": 0,
        "reasons_not_recorded": 0,
        "cancellations": 0,
    }
    for event in events:
        kind = event["kind"]
        detail = event.get("detail") or {}
        if kind in ("approved", "self_approved"):
            counts["approvals"] += 1
        if kind == "self_approved":
            counts["self_approvals"] += 1
        if kind == "rejected":
            counts["rejections"] += 1
        if kind == "tb_submitted" and detail.get("on_behalf") == "Yes":
            counts["on_behalf_uploads"] += 1
        if kind == "signed_off" and detail.get("signoff_status") == "Acknowledged":
            counts["acknowledgements"] += 1
        if kind == "signed_off" and detail.get("signoff_status") == "Overridden":
            counts["overrides"] += 1
        if kind in _REOPENED_KINDS:
            counts["reopenings"] += 1
        if event.get("source") == close_event_model.BACKFILL:
            counts["recovered"] += 1
        if detail.get("reason_not_recorded"):
            counts["reasons_not_recorded"] += 1
        if kind == "approval_cancelled":
            counts["cancellations"] += 1

    return {
        "signoff": _signoff(events),
        "closed": _span(events, _CLOSED_KINDS, _REOPENED_KINDS),
        "locked": _span(events, _LOCKED_KINDS, _REOPENED_KINDS),
        "counts": counts,
    }


# --- story 10.2: filters, filter choices, CSV ------------------------------------

#: The ``entities`` filter value that selects group-level (blank-entity)
#: events. Parenthesised so it can never collide with an entity code.
GROUP_LEVEL = "(group)"

CSV_COLUMNS = (
    "event", "at", "fiscal_year", "fiscal_period", "kind", "entity",
    "actor", "actor_name", "actor_persona", "reference_doctype",
    "reference_name", "reason", "source", "detail",
)

_FORMULA_START = ("=", "+", "-", "@", "\t", "\r")


def _list_filter(field, value):
    """A JSON list string or a list of strings, as a set; blank or empty is
    None (no filter)."""
    if value is None or value == "":
        return None
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            raise ValueError("the %s filter is not a JSON list: %r." % (field, value))
    if not isinstance(value, (list, tuple)):
        raise ValueError("the %s filter is not a list: %r." % (field, value))
    if not all(isinstance(v, str) and v for v in value):
        raise ValueError("the %s filter holds a value that is not a name: %r." % (field, value))
    return set(value) or None


def _date_filter(field, value):
    if value is None or value == "":
        return None
    if isinstance(value, datetime.datetime):
        return value.date()
    if isinstance(value, datetime.date):
        return value
    try:
        return datetime.date.fromisoformat(str(value))
    except ValueError:
        raise ValueError("%s %r is not a date (YYYY-MM-DD)." % (field, value))


def parse_filters(kinds=None, actors=None, entities=None, date_from=None, date_to=None):
    """The trail filters, read and checked. See the module docstring."""
    filters = {
        "kinds": _list_filter("kind", kinds),
        "actors": _list_filter("actor", actors),
        "entities": _list_filter("entity", entities),
        "date_from": _date_filter("date_from", date_from),
        "date_to": _date_filter("date_to", date_to),
    }
    unknown = sorted((filters["kinds"] or set()) - set(close_event_model.KINDS))
    if unknown:
        raise ValueError("unknown Close Event kind %s." % ", ".join(repr(k) for k in unknown))
    if filters["date_from"] and filters["date_to"] and filters["date_from"] > filters["date_to"]:
        raise ValueError("the date range runs backwards: %s is after %s."
                         % (filters["date_from"].isoformat(), filters["date_to"].isoformat()))
    return filters


def _day(at):
    if isinstance(at, datetime.datetime):
        return at.date()
    if isinstance(at, datetime.date):
        return at
    return datetime.date.fromisoformat(str(at)[:10])


def _matches(event, filters):
    if filters["kinds"] is not None and event["kind"] not in filters["kinds"]:
        return False
    if filters["actors"] is not None and event.get("actor") not in filters["actors"]:
        return False
    if filters["entities"] is not None:
        entity = event.get("entity") or GROUP_LEVEL
        if entity not in filters["entities"]:
            return False
    if filters["date_from"] is not None or filters["date_to"] is not None:
        day = _day(event["at"])
        if filters["date_from"] is not None and day < filters["date_from"]:
            return False
        if filters["date_to"] is not None and day > filters["date_to"]:
            return False
    return True


def filtered(events, filters):
    """The ``events`` every filter in ``filters`` (``parse_filters``'s
    result) matches, in the input order. Only ever cuts."""
    return [e for e in events if _matches(e, filters)]


def options(events):
    """The filter choices ``events`` offer: sorted distinct kinds, actors
    and entities (blank entity as ``GROUP_LEVEL``)."""
    return {
        "kinds": sorted({e["kind"] for e in events}),
        "actors": sorted({e["actor"] for e in events if e.get("actor")}),
        "entities": sorted({e.get("entity") or GROUP_LEVEL for e in events}),
    }


def _cell(value):
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, sort_keys=True)
    if isinstance(value, str):
        if value.startswith(_FORMULA_START):
            return "'" + value
        return value
    return str(value)


def csv_text(rows):
    """The CSV of ``rows`` (each ``trail_api``'s event shape plus
    ``fiscal_year`` and ``fiscal_period``): the ``CSV_COLUMNS`` header, then
    one line per row in the given order."""
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(CSV_COLUMNS)
    source_key = {"event": "name"}
    for row in rows:
        writer.writerow([_cell(row[source_key.get(col, col)]) for col in CSV_COLUMNS])
    return out.getvalue()

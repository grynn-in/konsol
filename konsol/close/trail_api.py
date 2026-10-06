"""Audit trail endpoint for the close app (konsol#305 T07b; story 10.1;
#297 R-decision "auditors are Viewers with audit trail"; amended 2 Oct by
#305-W2-9; amended by T07c).

``get_trail(fiscal_year, fiscal_period, ...filters)`` (GET) is the read
endpoint behind the Audit trail board (board 8): the period, the summary
(``trail_model.summary``) and the events (``trail_model.ordered``), each
with its actor's full name resolved and its datetimes as ISO strings.

T07c: the summary's actors (``signoff.by``, ``closed.by``, ``locked.by``)
are resolved to ``by_name``/``by_missing`` the same way an event's
``actor`` is, from the one ``User`` read ``_event_out`` already does --
every ``by`` names an actor of one of the scoped events, so this adds no
second read.

The Entity Accountant is not admitted: the trail holds every entity's
events (E10-P8), the boundary #305-P21 draws for the journal too.

Entity scope (#305-W2-9): the events (the period's own, plus that year's
``fiscal_period == 0`` events) are scoped to
``entity_permissions.allowed_entity_codes()`` through ``trail_model.visible``
before the summary is computed, the same cut ``period_grid`` and
``get_my_work`` apply. A group-level event (blank ``entity``: rates,
journals, period, year and sign-off events) is always visible. The response
carries ``hidden``, the count the caller's scope dropped -- never the
events themselves, so a count cannot reveal a hidden entity's activity
beyond that number.

Story 10.2: both endpoints take the filters (kind, actor, entity, date
range; ``trail_model.parse_filters``) and apply them after the scope cut,
so a filter only ever narrows. ``export_trail_csv`` (GET) returns the same
filtered events as a CSV download (``trail_model.CSV_COLUMNS``), built from
the same read as ``get_trail``.
"""
import datetime
import json

import frappe

from konsol import period_status
from konsol.close import trail_model
from konsol.close.timefmt import zoned_iso
from konsol.entity_permissions import allowed_entity_codes

#: The Close Event fields the trail reads, in the doctype's field_order,
#: plus ``name``.
EVENT_FIELDS = [
    "fiscal_year", "fiscal_period", "kind", "entity",
    "reference_doctype", "reference_name", "actor", "at",
    "reason", "detail", "source", "name",
]


def _iso(value):
    """A datetime with the site's UTC offset (mirrors signoff_api.py
    ``_iso``); a date as its plain ISO string; anything else as a string, or
    None for a blank value."""
    if isinstance(value, datetime.datetime):
        return zoned_iso(value, frappe.utils.get_system_timezone())
    if isinstance(value, datetime.date):
        return value.isoformat()
    return None if value in (None, "") else str(value)


def _detail(row):
    """``row["detail"]`` parsed, or None for a blank value. Refuses loudly
    on a row whose detail is not JSON -- it is never silently skipped."""
    raw = row.get("detail")
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        frappe.throw(
            "Close Event %s has unreadable detail; report it to the System Manager."
            % row["name"]
        )


def _iso_span(span):
    if not span:
        return span
    span = dict(span)
    span["at"] = _iso(span.get("at"))
    return span


def _with_by_name(d, users):
    """Adds ``by_name``/``by_missing`` next to ``d["by"]`` (T07c), resolved
    from the same single User lookup ``_event_out`` uses for ``actor`` --
    the actor named in ``by`` always belongs to one of ``events`` (the
    summary is computed over the same scoped list), so this never issues a
    second User read. A deleted actor shows the id itself, with
    ``by_missing: True``, exactly as a deleted event actor does. ``d`` with
    no ``by`` key (``{"state": "none"}``), or None (no close/lock span), is
    returned unchanged."""
    if not d or "by" not in d:
        return d
    d = dict(d)
    by = d.get("by")
    user = users.get(by)
    d["by_name"] = user["full_name"] if user else by
    d["by_missing"] = user is None
    return d


def _iso_summary(summary, users):
    """``trail_model.summary``'s datetimes (``signoff.at``, ``closed.at``,
    ``locked.at``) as ISO strings, and its actors (``signoff.by``,
    ``closed.by``, ``locked.by``) resolved to names like the events
    (T07c)."""
    summary = dict(summary)
    signoff = dict(summary["signoff"])
    if "at" in signoff:
        signoff["at"] = _iso(signoff.get("at"))
    summary["signoff"] = _with_by_name(signoff, users)
    summary["closed"] = _with_by_name(_iso_span(summary.get("closed")), users)
    summary["locked"] = _with_by_name(_iso_span(summary.get("locked")), users)
    return summary


def _event_out(event, users):
    """One event in the response shape: names resolved, ``at`` an ISO
    string. An actor with no User row (deleted) shows the id itself, with
    ``actor_missing: True`` -- visible, not guessed."""
    actor = event.get("actor")
    user = users.get(actor)
    detail = event.get("detail")
    return {
        "name": event["name"],
        "kind": event["kind"],
        "entity": event.get("entity"),
        "reference_doctype": event.get("reference_doctype"),
        "reference_name": event.get("reference_name"),
        "actor": actor,
        "actor_name": user["full_name"] if user else actor,
        "actor_missing": user is None,
        "actor_persona": (detail or {}).get("actor_persona"),
        "at": _iso(event.get("at")),
        "reason": event.get("reason"),
        "detail": detail,
        "source": event.get("source"),
    }


def _filters(kinds, actors, entities, date_from, date_to):
    """``trail_model.parse_filters``, its ValueError refused loudly."""
    try:
        return trail_model.parse_filters(kinds, actors, entities, date_from, date_to)
    except ValueError as e:
        frappe.throw(str(e))


def _filters_out(filters):
    """The applied filters, echoed JSON-safe: sorted lists ([] = none) and
    ISO dates (None = open)."""
    return {
        "kinds": sorted(filters["kinds"] or ()),
        "actors": sorted(filters["actors"] or ()),
        "entities": sorted(filters["entities"] or ()),
        "date_from": filters["date_from"].isoformat() if filters["date_from"] else None,
        "date_to": filters["date_to"].isoformat() if filters["date_to"] else None,
    }


def _scoped_trail(fiscal_year, fiscal_period, kinds, actors, entities, date_from, date_to):
    """The one read behind both endpoints: the period row, the scoped
    events (``trail_model.visible``) newest first, the hidden count, the
    filtered events, the filters and the actors' User rows. The filters
    run after the scope cut, so they can only narrow what the caller may
    see."""
    row = period_status.period_row(fiscal_year, fiscal_period)
    fy, fp = row["fiscal_year"], row["fiscal_period"]
    filters = _filters(kinds, actors, entities, date_from, date_to)

    rows = frappe.get_all(
        "Close Event",
        filters={"fiscal_year": fy, "fiscal_period": ["in", [fp, 0]]},
        fields=EVENT_FIELDS,
        order_by="at desc, name desc",
        limit_page_length=0,
    )
    events = [dict(r, detail=_detail(r)) for r in rows]

    allowed = allowed_entity_codes()
    events, hidden = trail_model.visible(events, allowed)
    events = trail_model.ordered(events)
    shown = trail_model.filtered(events, filters)

    actors_seen = sorted({e["actor"] for e in events if e.get("actor")})
    users = {}
    if actors_seen:
        users = {
            u["name"]: u
            for u in frappe.get_all("User", filters={"name": ["in", actors_seen]},
                                    fields=["name", "full_name"])
        }
    return row, events, hidden, shown, filters, users


def _options_out(events, users):
    """``trail_model.options`` over the scoped events, each actor with its
    resolved name (a deleted actor shows the id, ``actor_missing: True``)."""
    opts = trail_model.options(events)
    opts["actors"] = [
        {
            "actor": actor,
            "actor_name": users[actor]["full_name"] if actor in users else actor,
            "actor_missing": actor not in users,
        }
        for actor in opts["actors"]
    ]
    return opts


@frappe.whitelist(methods=["GET"])
def get_trail(fiscal_year, fiscal_period, kinds=None, actors=None, entities=None,
              date_from=None, date_to=None):
    """The period's audit trail: ``{"period", "summary", "events",
    "hidden", "total", "options", "filters"}``. Read-only.

    Story 10.2: ``kinds``, ``actors`` and ``entities`` (JSON lists; an
    entity of ``trail_model.GROUP_LEVEL`` selects group-level events) and
    ``date_from``/``date_to`` (inclusive ISO dates) cut ``events`` after the
    entity scope. ``summary``, ``total`` (the scoped count), ``options``
    (the scoped choices) and ``hidden`` describe the whole scoped period,
    not the filtered view.

    Refuses an undeclared period, or one that is not an integer
    (PeriodNotDeclared, propagated from ``period_status.period_row``), and
    a bad filter (ValidationError naming it); the Entity Accountant is
    refused by ``only_for`` (E10-P8).
    """
    # A01: a literal, so the endpoint contract test can read it; the Entity
    # Accountant is excluded (E10-P8): the trail holds every entity's
    # events, not only theirs.
    frappe.only_for(("EPM Admin", "EPM Analyst", "EPM User", "System Manager"))
    row, events, hidden, shown, filters, users = _scoped_trail(
        fiscal_year, fiscal_period, kinds, actors, entities, date_from, date_to)

    return {
        "period": {
            "fiscal_year": row["fiscal_year"],
            "fiscal_period": row["fiscal_period"],
            "code": row["code"],
            "status": row["status"],
        },
        "summary": _iso_summary(trail_model.summary(events), users),
        "events": [_event_out(e, users) for e in shown],
        "hidden": hidden,
        "total": len(events),
        "options": _options_out(events, users),
        "filters": _filters_out(filters),
    }


@frappe.whitelist(methods=["GET"])
def export_trail_csv(fiscal_year, fiscal_period, kinds=None, actors=None, entities=None,
                     date_from=None, date_to=None):
    """The filtered, scoped trail as a CSV download (story 10.2).

    Takes ``get_trail``'s arguments and writes exactly the events
    ``get_trail`` returns for them, in the same order -- built here from the
    same read, so the file can never hold an event the caller's entity
    scope hides. Columns: ``trail_model.CSV_COLUMNS`` (documented in
    trail_model.py). Named ``audit-trail-FY<year>-P<period>.csv``. Read-only;
    the same roles as ``get_trail``.
    """
    # A01: a literal, the same roles as get_trail (E10-P8).
    frappe.only_for(("EPM Admin", "EPM Analyst", "EPM User", "System Manager"))
    row, _events, _hidden, shown, _filters, users = _scoped_trail(
        fiscal_year, fiscal_period, kinds, actors, entities, date_from, date_to)

    rows = [
        dict(_event_out(e, users), fiscal_year=e["fiscal_year"], fiscal_period=e["fiscal_period"])
        for e in shown
    ]
    frappe.response["filename"] = "audit-trail-FY%d-P%02d.csv" % (
        int(row["fiscal_year"]), int(row["fiscal_period"]))
    frappe.response["filecontent"] = trail_model.csv_text(rows)
    frappe.response["content_type"] = "text/csv; charset=utf-8"
    frappe.response["type"] = "download"

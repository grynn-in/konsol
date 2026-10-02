"""Audit trail endpoint for the close app (konsol#305 T07b; story 10.1;
#297 R-decision "auditors are Viewers with audit trail"; amended 2 Oct by
#305-W2-9).

``get_trail(fiscal_year, fiscal_period)`` (GET) is the one read endpoint
behind the Audit trail board (board 8): the period, the summary
(``trail_model.summary``) and the events (``trail_model.ordered``), each
with its actor's full name resolved and its datetimes as ISO strings.

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


def _iso_summary(summary):
    """``trail_model.summary``'s datetimes (``signoff.at``, ``closed.at``,
    ``locked.at``) as ISO strings."""
    summary = dict(summary)
    signoff = dict(summary["signoff"])
    if "at" in signoff:
        signoff["at"] = _iso(signoff.get("at"))
    summary["signoff"] = signoff
    summary["closed"] = _iso_span(summary.get("closed"))
    summary["locked"] = _iso_span(summary.get("locked"))
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


@frappe.whitelist(methods=["GET"])
def get_trail(fiscal_year, fiscal_period):
    """The period's audit trail: ``{"period", "summary", "events",
    "hidden"}``. Read-only.

    Refuses an undeclared period, or one that is not an integer
    (PeriodNotDeclared, propagated from ``period_status.period_row``); the
    Entity Accountant is refused by ``only_for`` (E10-P8).
    """
    # A01: a literal, so the endpoint contract test can read it; the Entity
    # Accountant is excluded (E10-P8): the trail holds every entity's
    # events, not only theirs.
    frappe.only_for(("EPM Admin", "EPM Analyst", "EPM User", "System Manager"))
    row = period_status.period_row(fiscal_year, fiscal_period)
    fy, fp = row["fiscal_year"], row["fiscal_period"]

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

    actors = sorted({e["actor"] for e in events if e.get("actor")})
    users = {}
    if actors:
        users = {
            u["name"]: u
            for u in frappe.get_all("User", filters={"name": ["in", actors]},
                                    fields=["name", "full_name"])
        }

    return {
        "period": {
            "fiscal_year": fy,
            "fiscal_period": fp,
            "code": row["code"],
            "status": row["status"],
        },
        "summary": _iso_summary(trail_model.summary(events)),
        "events": [_event_out(e, users) for e in events],
        "hidden": hidden,
    }

"""The close audit trail's one writer (konsol#305 T02a, #298 story 10.1,
#305-W2-1).

- ``record(...)``: a live event, written at the action. The actor is the
  session user, ``at`` is now, and ``detail.actor_persona`` is the persona of
  the actor's roles at the time (E10-P7).
- ``record_backfill(event)``: an event recovered from an old record by the
  backfill patch (T06b); its actor, time and detail come from that record.
- Both check the event with ``close_event_model.event_problems`` and refuse
  a malformed one loudly (a caller bug), then insert through ``_insert``,
  the only place a Close Event is inserted. ``_insert`` enters the
  controller's ``writing()``; outside it the controller refuses (T01b).
- **No commit and no try/except.** The event is inserted in the caller's
  transaction: if it fails, the action fails and rolls back, so there is
  never an action without its event or an event without its action.
- ``period_of(doc)``: the period an approval document's event belongs to
  (#305-W2-5, Deepak Pai 2 Oct 2026). A date-keyed record (Ownership
  Period, Historical Equity Rate) belongs to the first declared period it
  affects, the rule ``period_status.assert_open_between`` gates on; that may
  be a Closing period. With no such period the approval is refused.
  Rejected: the period containing the approval date.
- ``reminders(keys, topic=None)``: the one reader of ``reminder_sent``
  events (Y53, C-R6). Every surface's reminder count comes from here, never
  from Notification Log (purged after 180 days, M6).
- ``entity_of(doc)``: the entity the trail scopes the event by (#305-W2-9).
  Group-level documents give None, so every trail reader sees them.

A source test (test_close_event_writer.py) pins that nothing else writes,
changes or deletes a Close Event.
"""
import json

import frappe

from konsol import period_status
from konsol.close import close_event_model, period_model
from konsol.consolidation.doctype.close_event import close_event as close_event_controller

#: Approval and close documents that carry their own period key.
PERIOD_FIELD_DOCTYPES = (
    "Group Exchange Rate",
    "IC Balance",
    "Consolidation Journal",
    "Trial Balance Submission",
    "TB Exception",
)

#: Date-keyed approval documents: doctype -> the date field that decides the
#: first period the record affects.
DATE_FIELDS = {
    "Ownership Period": "effective_date",
    "Historical Equity Rate": "rate_date",
}

#: Every approval doctype (close_policy_model.APPROVAL_DOCTYPES) -> the field
#: naming the entity its event is scoped by, or None for a group-level
#: document (top Problems W2-P3).
ENTITY_FIELDS = {
    "Ownership Period": "data_area_id",
    "Historical Equity Rate": "data_area_id",
    "Business Combination": "acquired_entity",
    "Business Disposal": "disposed_entity",
    "Group Exchange Rate": None,
    "Consolidation Journal": None,
    "IC Balance": None,
}


def record(kind, fiscal_year, fiscal_period, reference_doctype=None, reference_name=None,
           reason=None, detail=None, entity=None):
    """Write one live Close Event in the caller's transaction; return its name."""
    event = {
        "kind": kind,
        "fiscal_year": fiscal_year,
        "fiscal_period": fiscal_period,
        "entity": entity,
        "reference_doctype": reference_doctype,
        "reference_name": reference_name,
        "actor": frappe.session.user,
        "at": frappe.utils.now_datetime(),
        "reason": (reason or "").strip() or None,
        "detail": dict(detail or {}, actor_persona=period_model.persona(frappe.get_roles())),
        "source": close_event_model.LIVE,
    }
    _refuse_problems(event)
    return _insert(event)


def record_backfill(event):
    """Write one recovered Close Event, as the backfill read it; return its name."""
    if event.get("source") != close_event_model.BACKFILL:
        frappe.throw("record_backfill writes only backfill events; a live event goes through record().")
    _refuse_problems(event)
    return _insert(event)


def _refuse_problems(event):
    problems = close_event_model.event_problems(event)
    if problems:
        frappe.throw("; ".join(problems))


def _insert(event):
    with close_event_controller.writing():
        doc = frappe.get_doc(dict(
            event,
            doctype="Close Event",
            detail=close_event_model.detail_json(event.get("detail")),
        )).insert(ignore_permissions=True)
    return doc.name


def period_of(doc):
    """``(fiscal_year, fiscal_period)`` the event about ``doc`` belongs to."""
    doctype = doc.doctype
    if doctype in PERIOD_FIELD_DOCTYPES:
        return int(doc.fiscal_year), int(doc.fiscal_period)
    if doctype == "Business Combination":
        return _key(doc._acquisition_period())
    if doctype == "Business Disposal":
        return _key(doc._disposal_period())
    if doctype in DATE_FIELDS:
        date = getattr(doc, DATE_FIELDS[doctype])
        start = period_status.first_period_affected(date)
        if start is None:
            frappe.throw(
                f"{doctype} {doc.name} changes no declared fiscal period (its date {date} is "
                "after the last one): declare the period in EPM Fiscal Year first.")
        # The first period the record affects, whatever its type: a Regular
        # row only wins a tie on the same start date (#305-W2-5).
        rows = frappe.db.sql(
            "SELECT y.fiscal_year, p.fiscal_period "
            "FROM `tabEPM Fiscal Year Period` p "
            "JOIN `tabEPM Fiscal Year` y ON y.name = p.parent "
            "WHERE p.parentfield = 'periods' AND p.start_date = %(start)s "
            "ORDER BY (p.period_type <> 'Regular'), y.fiscal_year LIMIT 1",
            {"start": start},
            as_dict=True,
        )
        return _key(rows[0])
    raise ValueError(f"period_of does not know where a {doctype} event belongs")


def latest_rejections(doctype, names):
    """``{name: {"reason", "actor", "at"}}``: the newest ``rejected`` event
    per document name in ``names`` (#305-A03, E6-P3, E6-P11). Empty
    ``names`` makes no read and returns ``{}``."""
    if not names:
        return {}
    rows = frappe.get_all(
        "Close Event",
        filters={
            "kind": "rejected",
            "reference_doctype": doctype,
            "reference_name": ["in", sorted(names)],
        },
        fields=["reference_name", "actor", "at", "reason"],
        order_by="at desc, name desc",
        limit_page_length=0,
    )
    result = {}
    for row in rows:
        result.setdefault(row["reference_name"], {
            "reason": row["reason"],
            "actor": row["actor"],
            "at": row["at"],
        })
    return result


def reminders(keys, topic=None):
    """The ``reminder_sent`` events of the periods ``keys`` (a list of
    ``(fiscal_year, fiscal_period)``), each ``{name, fiscal_year,
    fiscal_period, entity, actor, at, detail}`` with ``detail`` parsed into a
    dict; only ``detail.topic == topic`` when ``topic`` is given (Y53).

    No keys makes no read and returns ``[]``. A blank detail raises
    ValueError naming the event: a reminder is never read as one to nobody.
    """
    wanted = {(int(fy), int(fp)) for fy, fp in keys}
    if not wanted:
        return []
    rows = frappe.get_all(
        "Close Event",
        filters={"kind": "reminder_sent", "fiscal_year": ["in", sorted({fy for fy, _ in wanted})]},
        fields=["name", "fiscal_year", "fiscal_period", "entity", "actor", "at", "detail"],
        limit_page_length=0,
    )
    result = []
    for row in rows:
        if _key(row) not in wanted:
            continue
        raw = row.get("detail")
        if not isinstance(raw, dict) and not (raw or "").strip():
            raise ValueError(f"Close Event {row.get('name')} is a reminder with no detail")
        detail = raw if isinstance(raw, dict) else json.loads(raw)
        if topic is not None and detail.get("topic") != topic:
            continue
        result.append(dict(row, detail=detail))
    return result


def entity_of(doc):
    """The entity the trail scopes an event about ``doc`` by, or None for a
    group-level document."""
    if doc.doctype not in ENTITY_FIELDS:
        raise ValueError(f"entity_of does not know the entity of a {doc.doctype}")
    field = ENTITY_FIELDS[doc.doctype]
    return (getattr(doc, field) or None) if field else None


def _key(row):
    return int(row["fiscal_year"]), int(row["fiscal_period"])

"""konsol#305 story 1.5 (#305-1.5-1): the Remind rules, pure.

No frappe and no konsol import: the endpoint (remind_api) and every reader of
reminders (TB rows, the period grid, My work, the IC panel) load and call it.

- Topics are a closed set, ``tb`` and ``ic`` (C-R1).
- Recipients are enabled users, other than Administrator and Guest, with a
  User Permission ``allow = "Entity"`` DIRECTLY on the entity (C-R2). Decision
  #305-Q2-1 (Deepak Pai, 7 Oct 2026): permissions on ancestor nodes do not
  count. Rejected: #305-Q2-2, include users permitted on parent nodes.
- ``refusal`` returns the first of the C-R4 refusals, in a fixed order, or
  None. There is no throttle (decision).
- ``subject`` is the fixed C-R5 text; there is no free-text note.
- ``summary`` gives "count and last" per (fy, fp, entity, topic) from
  ``reminder_sent`` events (C-R6). The latest is chosen by comparing
  datetimes in UTC, never text (review-w5 S13).
"""
from datetime import datetime, timezone

TOPICS = ("tb", "ic")
TOPIC_LABEL = {"tb": "Trial balance", "ic": "Intercompany"}
REMIND_ROLES = ("EPM Admin", "EPM Analyst", "System Manager")

_NEVER_RECIPIENTS = ("Administrator", "Guest")
_SUBJECT_TAIL = {
    "tb": "trial balance is still missing",
    "ic": "intercompany difference needs attention",
}
_LINK_SCREEN = {"tb": "trial-balances", "ic": "intercompany"}

UNKNOWN_TOPIC = "Remind about a trial balance (tb) or an intercompany difference (ic)."


def _check_topic(topic):
    if topic not in TOPICS:
        raise ValueError("Unknown reminder topic %r: %s" % (topic, UNKNOWN_TOPIC))


def recipients(entity, permissions, users):
    """Sorted user ids with a direct Entity permission on ``entity``.

    ``permissions``: rows ``{user, allow, for_value}``. ``users``:
    ``{name: {"enabled": 0|1}}``. A permitted user missing from ``users``
    raises: whether they are enabled is never guessed.
    """
    out = set()
    for row in permissions:
        if row.get("allow") != "Entity" or row.get("for_value") != entity:
            continue
        user = row.get("user")
        if user in _NEVER_RECIPIENTS:
            continue
        if user not in users:
            raise ValueError("User %r has a User Permission on Entity %s but no user record "
                             "was read: cannot tell whether they are enabled." % (user, entity))
        if int(users[user]["enabled"]) == 1:
            out.add(user)
    return sorted(out)


def refusal(entity, topic, period_status, allowed, recipients, tb_in, period_text):
    """None, or the one C-R4 sentence that refuses this reminder.

    Order: unknown topic; period not Open; caller cannot see the entity
    (``allowed`` is a set, None means unrestricted); topic tb already in;
    no recipients.
    """
    if topic not in TOPICS:
        return UNKNOWN_TOPIC
    if period_status != "Open":
        return "%s is %s: remind only in an Open period." % (period_text, period_status)
    if allowed is not None and entity not in allowed:
        return ("%s is not one of your entities: you can remind only about entities "
                "you can see." % entity)
    if topic == "tb" and tb_in:
        return "%s's %s trial balance is already in: nothing to remind." % (entity, period_text)
    if not recipients:
        return ("No one is named for %s: a System Manager gives a user a User Permission "
                "on Entity %s before it can be reminded." % (entity, entity))
    return None


def subject(sender_name, entity, topic, period_text):
    """The fixed C-R5 subject."""
    _check_topic(topic)
    return "Reminder from %s: %s's %s %s" % (sender_name, entity, period_text,
                                             _SUBJECT_TAIL[topic])


def link(fy, fp, topic):
    """The SPA path a reminder points to."""
    _check_topic(topic)
    return "/close/%s/%s/%s" % (int(fy), int(fp), _LINK_SCREEN[topic])


def _instant(at, name):
    if not isinstance(at, datetime):
        raise ValueError("Close Event %s: reminder time %r is not a datetime." % (name, at))
    if at.tzinfo is not None and at.utcoffset() is not None:
        return at.astimezone(timezone.utc).replace(tzinfo=None)
    return at


def summary(events):
    """``{(fy, fp, entity, topic): {"count", "last_at", "last_by"}}``.

    ``events``: ``{name, fiscal_year, fiscal_period, entity, detail:{topic},
    actor, at}``. An unknown or missing topic, or an ``at`` that is not a
    datetime, raises ValueError naming the event.
    """
    out = {}
    best = {}
    for ev in events:
        name = ev.get("name")
        topic = (ev.get("detail") or {}).get("topic")
        if topic not in TOPICS:
            raise ValueError("Close Event %s: reminder topic %r is not one of %s."
                             % (name, topic, ", ".join(TOPICS)))
        instant = _instant(ev.get("at"), name)
        key = (int(ev["fiscal_year"]), int(ev["fiscal_period"]), ev["entity"], topic)
        entry = out.get(key)
        if entry is None:
            out[key] = {"count": 1, "last_at": ev["at"], "last_by": ev["actor"]}
            best[key] = instant
            continue
        entry["count"] += 1
        if instant > best[key]:
            best[key] = instant
            entry["last_at"] = ev["at"]
            entry["last_by"] = ev["actor"]
    return out


def reminded_text(entry, name_of, format_at):
    """``"Reminded 2× · last <at> by <full name>"``.

    ``name_of(user)`` gives a full name; None raises (never shows a raw id).
    ``format_at(datetime)`` is the caller's formatting: the caller zones it.
    """
    full_name = name_of(entry["last_by"])
    if not full_name:
        raise ValueError("No full name for user %r, who sent the last reminder."
                         % entry["last_by"])
    return "Reminded %d× · last %s by %s" % (int(entry["count"]), format_at(entry["last_at"]),
                                             full_name)

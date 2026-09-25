"""My-work setup gaps: one configuration gap is one item. Pure (konsol#305 A14; stories 1.2, 0.4).

Imports nothing from frappe or konsol; the caller gathers ``facts``:

- ``first_close``: the first close period ``(fiscal_year, fiscal_period)``
  from Close Settings, or None. A key with a 0 part is unset (Close Settings
  Int fields read back as 0) and counts as None.
- ``chart_published``: bool; False when ``group_chart.chart_accounts()`` is
  empty.
- ``frequency_missing``: in-scope entities with no ``reporting_frequency``.
- ``ownership_missing``: in-scope leaves with no ownership period covering the
  period (in scope: Active, not a group).
- ``accountants_without_entities``: enabled Entity Accountants with no Entity
  user permission.

Rules:

- One gap is ONE item that lists everyone affected (#291: never one item per
  entity per month). An empty list gives no item.
- Ids are fixed (``gap:<name>``) and items come in ``GAPS`` order.
- Every item is ``kind "blocking"``, names the owner role that can fix it,
  and its ``action`` opens the Desk page (story 0.4: the Desk only for
  configuration gaps).
- A missing fact raises ValueError: nothing is guessed.
"""

GAPS = ("first_close", "chart", "frequency", "ownership", "accountants")
FACT_KEYS = ("first_close", "chart_published", "frequency_missing", "ownership_missing",
             "accountants_without_entities")


def _declared(first_close):
    if first_close is None:
        return False
    year, period = first_close
    return bool(year) and bool(period)


def _entities(n):
    return f"{n} entity" if n == 1 else f"{n} entities"


def _item(gap, title, detail, owner, desk, entities=(), users=()):
    return {
        "id": f"gap:{gap}",
        "kind": "blocking",
        "title": title,
        "detail": detail,
        "owner": owner,
        "entities": sorted(set(entities)),
        "users": sorted(set(users)),
        "action": {"desk": desk},
    }


def setup_gap_items(facts):
    missing = [k for k in FACT_KEYS if k not in facts]
    if missing:
        raise ValueError(f"setup_gap_items: missing fact(s) {', '.join(missing)}")
    if not isinstance(facts["chart_published"], bool):
        raise ValueError("setup_gap_items: chart_published must be True or False")

    items = []
    if not _declared(facts["first_close"]):
        items.append(_item("first_close", "First close period not declared",
                           "Set the first close period in Close Settings.", "EPM Admin",
                           "/app/close-settings"))
    if not facts["chart_published"]:
        items.append(_item("chart", "Group chart not published",
                           "Publish the Main Accounts of the group chart.", "EPM Admin",
                           "/app/main-account"))
    freq = sorted(set(facts["frequency_missing"] or ()))
    if freq:
        items.append(_item("frequency", f"Reporting frequency missing for {_entities(len(freq))}",
                           ", ".join(freq), "EPM Admin", "/app/entity", entities=freq))
    own = sorted(set(facts["ownership_missing"] or ()))
    if own:
        items.append(_item("ownership", f"Ownership missing for {_entities(len(own))}",
                           ", ".join(own), "EPM Admin", "/app/ownership-period", entities=own))
    users = sorted(set(facts["accountants_without_entities"] or ()))
    if users:
        n = len(users)
        items.append(_item("accountants", f"{n} Entity Accountant{'s' if n > 1 else ''} with no entity",
                           ", ".join(users), "System Manager", "/app/user", users=users))
    return items


# --- A20: per-role period items and ranking -----------------------------------
#
# ``period_items(persona, per_period, first_close)`` turns the facts of every
# open period into the items of one persona. ``per_period`` is keyed by the
# period key ``(fiscal_year, fiscal_period)``; each value holds PERIOD_KEYS.
#
# D5: My work spans every open period from the first close period on, and
# each item carries its period. Periods before the first close are history and
# never produce items. An undeclared first close raises ValueError: the caller
# shows ``gap:first_close`` instead of guessing.
#
# Rules:
# - Entity Accountant: one "Upload TB for <entity>" per ``my_missing`` entity
#   per period; blocking when the period has ended, else a todo. Only
#   ``my_missing`` is read, so another entity's item never reaches them.
# - Group Accountant: "Run checks" (todo) when checks are not_run or stale;
#   "N checks failing" (blocking); "Waiting on N trial balances" (waiting).
# - Close Lead: "Rates missing (N)" and "Re-sign needed" (blocking); "Sign off
#   <code>" (todo) when checks are current, none fail, no rates are missing,
#   the period is not signed and no gate blocks. When a gate blocks, the item
#   is "Waiting on <earliest earlier open period>" instead, or "Waiting on the
#   sign-off gates" when no earlier open period explains it. The Close Lead
#   also waits on trial balances and on checks.
# - Viewer: no items.
# Actions are in-app screens, never the Desk (story 0.4).

CLOSE_LEAD = "close_lead"
GROUP_ACCOUNTANT = "group_accountant"
ENTITY_ACCOUNTANT = "entity_accountant"
VIEWER = "viewer"
PERSONAS = (CLOSE_LEAD, GROUP_ACCOUNTANT, ENTITY_ACCOUNTANT, VIEWER)

PERIOD_KEYS = ("code", "ended", "my_missing", "missing", "checks", "failed", "signoff",
               "gates_blocked", "rates_missing")
CHECK_STATES = ("not_run", "running", "stale", "failed", "current")
KINDS = ("blocking", "todo", "waiting")

#: Copied from assertion_run.SIGNED_STATES (assertion_run.py:224); not imported.
SIGNED_STATES = ("Signed Off", "Acknowledged", "Overridden")
RE_SIGN_NEEDED = "Re-sign Needed"

OWNERS = {
    CLOSE_LEAD: "EPM Admin",
    GROUP_ACCOUNTANT: "EPM Analyst",
    ENTITY_ACCOUNTANT: "Entity Accountant",
}


def _tbs(n):
    return "%d trial balance%s" % (n, "" if n == 1 else "s")


def _check_period(key, facts):
    absent = [k for k in PERIOD_KEYS if k not in facts]
    if absent:
        raise ValueError("period_items: %s is missing %s" % (key, ", ".join(absent)))
    if facts["checks"] not in CHECK_STATES:
        raise ValueError("period_items: %s has unknown checks state %r" % (key, facts["checks"]))


def _period_item(persona, key, facts, slug, kind, title, action):
    fy, fp = key
    return {
        "id": "%s:%d-%02d" % (slug, fy, fp),
        "kind": kind,
        "title": title,
        "period": {"fiscal_year": fy, "fiscal_period": fp, "code": facts["code"]},
        "owner": OWNERS[persona],
        "action": action,
    }


def _entity_accountant(key, facts):
    kind = "blocking" if facts["ended"] else "todo"
    items = []
    for entity in sorted(set(facts["my_missing"] or ())):
        item = _period_item(ENTITY_ACCOUNTANT, key, facts, "tb", kind, "Upload TB for %s" % entity,
                            {"screen": "trial-balances", "entity": entity})
        item["id"] += ":%s" % entity
        items.append(item)
    return items


def _group_accountant(key, facts):
    items = []
    p = GROUP_ACCOUNTANT
    if facts["checks"] in ("not_run", "stale"):
        items.append(_period_item(p, key, facts, "checks-run", "todo", "Run checks",
                                  {"screen": "checks"}))
    failed = int(facts["failed"] or 0)
    if failed:
        items.append(_period_item(p, key, facts, "checks-failing", "blocking",
                                  "%d check%s failing" % (failed, "" if failed == 1 else "s"),
                                  {"screen": "checks"}))
    missing = sorted(set(facts["missing"] or ()))
    if missing:
        items.append(_period_item(p, key, facts, "tbs-waiting", "waiting",
                                  "Waiting on %s" % _tbs(len(missing)),
                                  {"screen": "trial-balances"}))
    return items


def _close_lead(key, facts, earlier_open):
    items = []
    p = CLOSE_LEAD
    signoff = {"screen": "sign-off"}
    rates = int(facts["rates_missing"] or 0)
    if rates:
        items.append(_period_item(p, key, facts, "rates", "blocking",
                                  "Rates missing (%d)" % rates, signoff))
    resign = facts["signoff"] == RE_SIGN_NEEDED
    if resign:
        items.append(_period_item(p, key, facts, "resign", "blocking", "Re-sign needed", signoff))
    missing = sorted(set(facts["missing"] or ()))
    if missing:
        items.append(_period_item(p, key, facts, "tbs-waiting", "waiting",
                                  "Waiting on %s" % _tbs(len(missing)),
                                  {"screen": "trial-balances"}))
    failed = int(facts["failed"] or 0)
    if failed:
        items.append(_period_item(p, key, facts, "checks-waiting", "waiting",
                                  "Waiting on %d failing check%s" % (failed, "" if failed == 1 else "s"),
                                  {"screen": "checks"}))
    elif facts["checks"] != "current":
        items.append(_period_item(p, key, facts, "checks-waiting", "waiting", "Waiting on checks",
                                  {"screen": "checks"}))
    clean = facts["checks"] == "current" and not failed and not rates
    if clean and not resign and facts["signoff"] not in SIGNED_STATES:
        if not facts["gates_blocked"]:
            items.append(_period_item(p, key, facts, "signoff", "todo",
                                      "Sign off %s" % facts["code"], signoff))
        elif earlier_open:
            items.append(_period_item(p, key, facts, "signoff-wait", "waiting",
                                      "Waiting on %s" % earlier_open, signoff))
        elif not missing:
            items.append(_period_item(p, key, facts, "signoff-wait", "waiting",
                                      "Waiting on the sign-off gates", signoff))
    return items


def period_items(persona, per_period, first_close):
    """Items for ``persona`` across the open periods in ``per_period``."""
    if persona not in PERSONAS:
        raise ValueError("period_items: unknown persona %r" % (persona,))
    if not _declared(first_close):
        raise ValueError("period_items: the first close period is not declared")
    first = (int(first_close[0]), int(first_close[1]))
    periods = sorted(
        ((int(k[0]), int(k[1])), v) for k, v in (per_period or {}).items()
        if (int(k[0]), int(k[1])) >= first
    )
    for key, facts in periods:
        _check_period(key, facts)
    if persona == VIEWER:
        return []

    items = []
    earliest_code = None
    for key, facts in periods:
        if persona == ENTITY_ACCOUNTANT:
            items.extend(_entity_accountant(key, facts))
        elif persona == GROUP_ACCOUNTANT:
            items.extend(_group_accountant(key, facts))
        else:
            items.extend(_close_lead(key, facts, earliest_code))
        if earliest_code is None:
            earliest_code = facts["code"]
    return items


def _rank_key(item):
    period = item.get("period")
    if period is None:
        return (KINDS.index(item["kind"]), 0, 0, 0, "")
    return (KINDS.index(item["kind"]), 1, int(period["fiscal_year"]),
            int(period["fiscal_period"]), item["title"])


def rank(items):
    """Blocking, then todo, then waiting; older periods first, then by title.

    Setup-gap items (no period) come first within their kind, in their own
    order."""
    return sorted(items or (), key=_rank_key)

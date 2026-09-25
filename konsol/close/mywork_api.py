"""My work: every item the caller owns across the open periods (konsol#305 A29; stories 1.1-1.4, 0.3).

``get_my_work`` (GET) reads the site and passes it through the pure models:

- setup gaps (A14 ``mywork_model.setup_gap_items``), read as of today:
  the first close period (Close Settings; 0 read back = undeclared), whether
  the group chart is published, in-scope entities with a blank reporting
  frequency, Active leaf entities with no submitted ownership period covering
  today, and enabled Entity Accountants with no Entity user permission;
- period items (A20/A45 ``mywork_model.period_items``) for every Regular
  period that is Open, has started (``start_date <= today``) and is not
  history (on or after the first close period).

An undeclared first close makes ``period_items`` raise, so then only the
setup-gap items are returned: no period is computed or guessed.

Entity scope is a security boundary. An Entity Accountant's items name only
the entities ``entity_permissions.allowed_entity_codes()`` allows: the upload
items come from ``my_missing`` alone, the frequency and ownership gaps are cut
to those entities, and the gap naming other users is not shown. A Viewer has
no items (and no My work screen).

Per-period facts:

- ``missing``: ``signoff_gate.sign_off_problems`` completeness (expected
  entities, quarterly rule included, with no submitted TB or TB Exception);
  ``gates_blocked`` is any config gap, order or completeness problem;
- ``checks``: A13 ``staleness`` of the newest run against
  ``freshness_api.current_freshness()``; a current run that is Red or Error
  is ``failed``; ``failed`` counts the latest terminal run's failed and
  errored assertions; ``signoff`` is that run's sign-off status;
- ``rates_missing``: ``group_rates.rate_gate`` missing keys plus groups with
  no reporting currency. When the warehouse cannot answer, the Close Lead
  gets a blocking item carrying the error, and the period counts as blocked,
  so it is never offered for sign-off.

``counts.by_screen`` holds ``{count, blocking}`` for every screen the persona
sees (``SCREENS``, held equal to close-ui/src/nav.js by the test). My work
counts every item; another screen counts the items whose action opens it.
Read-only.

A53: every period item's ``period`` carries ``since`` — the ISO end date of
that period ("how long this period has been over"), so the screen can show an
age. A period row with no end date is a configuration problem and raises;
it is never given today's date. A setup-gap item has no period, so it carries
a top-level ``since: None`` with ``since_reason: "configuration gap"``.

A54: the result carries a top-level ``entities_assigned``: ``true`` or
``false`` for the Entity Accountant persona, ``None`` for every other
persona. It answers "does this user have any entity assigned at all", from
``entity_permissions.allowed_entity_codes()`` directly (``None``/non-empty =
true, empty set = false) — never from ``my_missing`` or any other per-period,
in-scope-only list, whose ``[]`` cannot tell "nothing assigned" from
"assigned, but none in scope this period". The screen uses this to avoid
telling an Entity Accountant with real assignments "No entities are assigned
to you" just because none of theirs falls in the current period.
"""
from datetime import date, datetime

import frappe

from konsol import entity_permissions, fiscal_calendar, group_chart, group_rates
from konsol.close import checks_model, mywork_model, period_model, signoff_gate, signoff_model
from konsol.close.freshness_api import current_freshness
from konsol.consolidation.doctype.assertion_run.assertion_run import latest_close_run

#: The close roles; the gate spells them out (the A01 contract reads a literal).
ALL_CLOSE_ROLES = ("EPM Admin", "EPM Analyst", "Entity Accountant", "EPM User", "System Manager")

#: Screens each persona sees, in order; mirrors close-ui/src/nav.js SCREENS_BY_PERSONA.
SCREENS = {
    period_model.CLOSE_LEAD: ("my-work", "trial-balances", "checks", "sign-off"),
    period_model.GROUP_ACCOUNTANT: ("my-work", "trial-balances", "checks", "sign-off"),
    period_model.ENTITY_ACCOUNTANT: ("my-work", "trial-balances"),
    period_model.VIEWER: ("trial-balances", "checks", "sign-off"),
}
MY_WORK = "my-work"
REGULAR = "Regular"
FAILING_RUNS = ("Red", "Error")


def _date(value):
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str) and value:
        return date.fromisoformat(value[:10])
    return None


def _first_close():
    return signoff_model.first_close_key((
        frappe.db.get_single_value("Close Settings", "first_close_fiscal_year"),
        frappe.db.get_single_value("Close Settings", "first_close_fiscal_period"),
    ))


# --- setup gaps ----------------------------------------------------------------

def _leaves():
    """``{entity: reporting_frequency}`` of the Active leaf entities."""
    return {
        e["name"]: e.get("reporting_frequency") or ""
        for e in frappe.get_all("Entity", filters={"is_group": 0, "status": "Active"},
                                fields=["name", "reporting_frequency"], limit_page_length=0)
    }


def _covered(start):
    """Entities with a submitted ownership period covering ``start``."""
    covered = set()
    for o in frappe.get_all(
        "Ownership Period",
        filters={"docstatus": 1, "effective_date": ["<=", start], "data_area_id": ["is", "set"]},
        fields=["data_area_id", "end_date"], limit_page_length=0,
    ):
        end = _date(o.get("end_date"))
        if end is None or end >= start:
            covered.add(o.get("data_area_id"))
    return covered


def _accountants_without_entities():
    holders = set(frappe.get_all("Has Role", filters={"parenttype": "User", "role": "Entity Accountant"},
                                 pluck="parent"))
    if not holders:
        return []
    enabled = set(frappe.get_all("User", filters={"name": ["in", sorted(holders)], "enabled": 1},
                                 pluck="name"))
    if not enabled:
        return []
    assigned = set(frappe.get_all("User Permission",
                                  filters={"allow": "Entity", "user": ["in", sorted(enabled)]},
                                  pluck="user"))
    return sorted(enabled - assigned)


def _mine(entities, allowed):
    return sorted(e for e in entities if allowed is None or e in allowed)


def _gap_facts(first_close, persona, allowed, today):
    leaves = _leaves()
    covered = _covered(today)
    frequency_missing = [e for e, f in leaves.items() if e in covered and not f]
    ownership_missing = [e for e in leaves if e not in covered]
    group = persona != period_model.ENTITY_ACCOUNTANT
    return {
        "first_close": first_close,
        "chart_published": bool(group_chart.chart_accounts()),
        "frequency_missing": sorted(frequency_missing) if group else _mine(frequency_missing, allowed),
        "ownership_missing": sorted(ownership_missing) if group else _mine(ownership_missing, allowed),
        "accountants_without_entities": _accountants_without_entities() if group else [],
    }


# --- per-period facts -----------------------------------------------------------

def _open_rows(first_close, today):
    """Regular rows that are Open, started and not history, oldest first."""
    rows = []
    for row in fiscal_calendar.fiscal_period_rows():
        key = (int(row["fiscal_year"]), int(row["fiscal_period"]))
        start = _date(row.get("start_date"))
        if (row.get("period_type") == REGULAR and row.get("status") == "Open"
                and start is not None and start <= today and key >= first_close):
            rows.append((key, row))
    return sorted(rows, key=lambda kr: kr[0])


def _newest_run(key):
    rows = frappe.get_all(
        "Assertion Run",
        filters={"fiscal_year": key[0], "fiscal_period": key[1]},
        fields=["name", "status", "completed_at"],
        order_by="creation desc", limit=1,
    )
    return rows[0] if rows else None


def _checks(key, as_of):
    state = checks_model.staleness(_newest_run(key), as_of)["state"]
    terminal = latest_close_run(key[0], key[1])
    failed = 0
    signoff = None
    if terminal:
        failed = int(terminal.get("failed") or 0) + int(terminal.get("errored") or 0)
        signoff = terminal.get("signoff_status")
        if state == "current" and terminal.get("status") in FAILING_RUNS:
            state = "failed"
    return state, failed, signoff or period_model.NOT_SIGNED_OFF


def _rates_error_item(key, code, error, end_date):
    fy, fp = key
    return {
        "id": "rates-error:%d-%02d" % key,
        "kind": "blocking",
        "title": "Rates cannot be checked: %s" % error,
        "detail": ("The warehouse could not say which currencies %s translates (%s). "
                   "Rebuild the consolidation, then open this again." % (code, error)),
        "period": {"fiscal_year": fy, "fiscal_period": fp, "code": code,
                  "since": end_date.isoformat()},
        "owner": mywork_model.OWNERS[mywork_model.CLOSE_LEAD],
        "action": {"screen": "sign-off"},
    }


def _period_facts(first_close, allowed, today):
    """``(per_period, extra_items)``; extra items are the rate-gate errors."""
    as_of_text = current_freshness()["as_of"]
    as_of = datetime.fromisoformat(as_of_text) if as_of_text else None
    per_period, extra = {}, []
    for key, row in _open_rows(first_close, today):
        code = row.get("period_code") or "FY%d P%02d" % key
        end_date = _date(row.get("end_date"))
        if end_date is None:
            # A53: the age shown for a period is its end date. A period row with
            # no end date is a configuration problem, never silently today's date.
            frappe.throw("%s has no end date: fix its row in EPM Fiscal Year." % code)
        problems = signoff_gate.sign_off_problems(key[0], key[1])
        completeness = problems.get("completeness") or {}
        missing = sorted(completeness.get("missing") or ())
        blocked = bool(problems.get("config_gaps") or problems.get("order") or completeness)
        checks, failed, signoff = _checks(key, as_of)
        rates_missing, error, blockers = group_rates.rate_gate(key[0], key[1])
        if error:
            extra.append(_rates_error_item(key, code, error, end_date))
            blocked = True
        per_period[key] = {
            "code": code,
            "ended": end_date < today,
            "status": row.get("status"),
            "my_missing": _mine(missing, allowed),
            "missing": missing,
            "checks": checks,
            "failed": failed,
            "signoff": signoff,
            "gates_blocked": blocked,
            "rates_missing": len(rates_missing or ()) + len(blockers or ()),
            "since": end_date.isoformat(),
        }
    return per_period, extra


# --- counts ----------------------------------------------------------------------

def _counts(items, persona):
    counts = {kind: sum(1 for i in items if i["kind"] == kind) for kind in mywork_model.KINDS}
    by_screen = {}
    for screen in SCREENS[persona]:
        on = items if screen == MY_WORK else [
            i for i in items if (i.get("action") or {}).get("screen") == screen]
        by_screen[screen] = {"count": len(on),
                             "blocking": sum(1 for i in on if i["kind"] == "blocking")}
    counts["by_screen"] = by_screen
    return counts


@frappe.whitelist(methods=["GET"])
def get_my_work():
    """``{items, counts:{blocking, todo, waiting, by_screen:{screen: {count, blocking}}}}``.

    Items are ranked blocking, todo, waiting; each period item carries its
    period. An undeclared first close gives only the setup-gap items.
    """
    frappe.only_for(("EPM Admin", "EPM Analyst", "Entity Accountant", "EPM User", "System Manager"))
    roles = [r for r in ALL_CLOSE_ROLES if r in set(frappe.get_roles(frappe.session.user))]
    persona = period_model.persona(roles)
    if persona == period_model.VIEWER:
        return {"items": [], "counts": _counts([], persona), "entities_assigned": None}

    today = frappe.utils.getdate()
    first_close = _first_close()
    allowed = entity_permissions.allowed_entity_codes()
    # A54: whether the Entity Accountant has any entity assigned at all,
    # independent of this period's scope. `allowed` is the caller's raw grant
    # (None = unrestricted, set() = none, a non-empty set = some); only the
    # Entity Accountant persona is ever restricted to a named set, so this is
    # never asked of anyone else.
    entities_assigned = ((allowed is None or bool(allowed))
                         if persona == period_model.ENTITY_ACCOUNTANT else None)
    items = mywork_model.setup_gap_items(_gap_facts(first_close, persona, allowed, today))
    if first_close is not None:
        per_period, extra = _period_facts(first_close, allowed, today)
        items.extend(mywork_model.period_items(persona, per_period, first_close))
        if persona == period_model.CLOSE_LEAD:
            items.extend(extra)
    items = mywork_model.rank(items)
    return {"items": items, "counts": _counts(items, persona), "entities_assigned": entities_assigned}

"""Konsol Setup checklist, pure (konsol#407 phase 4; modelled on tapr_compliance P72).

``konsol.setup.status()`` reads the site through the readers that already
hold each rule (the sign-off gate, the rate gate, the ISO currency check, the
freshness model, the group chart model, the intercompany setup gap) and
passes what they answer here. This module only turns those answers into
checklist steps; it decides no accounting rule of its own.

A step is ``{"key", "label", "state", "detail", "count", "route",
"route_options"}``:

- ``state`` is ``done``, ``partial`` or ``not_done``. ``partial`` means the
  step is started but something the detail names is still missing.
- ``detail`` is one or more plain sentences saying why. Server text, never
  HTML: the page escapes it.
- ``count`` is a whole number where one helps (entities, accounts), else None.
- ``route`` is the Desk route where the step is done, as
  ``frappe.set_route`` takes it (``["List", doctype]`` or ``["Form", single]``),
  or None when the caller cannot open it; ``route_options`` filters that
  list, or None.

Entity codes named in a detail are cut to ``allowed`` (``None``: every entity
visible; a set: only those), with the hidden count stated, so the page never
names an entity the caller may not see. Imports nothing from frappe or konsol.
"""
import re as _re

DONE = "done"
PARTIAL = "partial"
NOT_DONE = "not_done"
STATES = (DONE, PARTIAL, NOT_DONE)

#: How many names a detail lists before "and N more".
MAX_NAMES = 10

#: The steps, in the order a site sets up: key -> (label, route).
STEPS = (
    ("fiscal_calendar", "Fiscal calendar", ["List", "EPM Fiscal Year"]),
    ("consolidation_group", "Consolidation group", ["List", "Consolidation Group"]),
    ("statement_accounts", "Statement accounts (CTA and current-year result)", ["Form", "Close Settings"]),
    ("entities", "Entities", ["List", "Entity"]),
    ("chart_of_accounts", "Chart of accounts", ["List", "Main Account"]),
    ("dimensions", "Dimensions", ["List", "Dimension"]),
    ("currencies", "ISO currencies", ["List", "ISO Currency"]),
    ("intercompany", "Intercompany accounts", ["List", "Intercompany Account"]),
    ("users", "Users and roles", ["List", "User"]),
    ("trial_balance", "First trial balance", ["List", "Trial Balance Submission"]),
    ("rates", "Group exchange rates", ["List", "Group Exchange Rate"]),
    ("warehouse", "Warehouse", ["List", "Build Approval"]),
)
_LABELS = {key: label for key, label, _ in STEPS}
_ROUTES = {key: route for key, _, route in STEPS}

#: rate_gate's wording for an entity blocker (group_rates.rate_gate; a test
#: pins the two together), so the entity in it can be cut to ``allowed``.
ENTITY_BLOCKER = _re.compile(r"^Entity (.+) has no functional currency$")

_DEFAULT_ROUTE = object()


def _step(key, state, detail, count=None, route_options=None, route=_DEFAULT_ROUTE):
    if state not in STATES:
        raise ValueError("Unknown setup state %r; expected one of %s." % (state, ", ".join(STATES)))
    return {
        "key": key,
        "label": _LABELS[key],
        "state": state,
        "detail": detail,
        "count": count,
        "route": list(_ROUTES[key] if route is _DEFAULT_ROUTE else route) if route is not None else None,
        "route_options": route_options if route is not None else None,
    }


def _plural(n, singular, plural=None):
    return "%d %s" % (n, singular if n == 1 else (plural or singular + "s"))


def _names(names):
    names = sorted(names)
    shown = ", ".join(names[:MAX_NAMES])
    if len(names) > MAX_NAMES:
        shown += " and %d more" % (len(names) - MAX_NAMES)
    return shown


def _cut(codes, allowed):
    """(visible codes, hidden count)."""
    codes = sorted(set(codes or ()))
    if allowed is None:
        return codes, 0
    visible = [c for c in codes if c in allowed]
    return visible, len(codes) - len(visible)


def _entity_list(codes, allowed):
    """"A, B" or "A and 2 you cannot see" or "3 you cannot see"."""
    visible, hidden = _cut(codes, allowed)
    parts = [_names(visible)] if visible else []
    if hidden:
        parts.append("%d you cannot see" % hidden)
    return " and ".join(parts)


def _sentences(parts):
    out = []
    for p in parts:
        p = (p or "").strip()
        if not p:
            continue
        out.append(p if p[-1] in ".!?" else p + ".")
    return " ".join(out)


def unchecked(key, error):
    """A step whose reader failed: not done, and the failure named. Never a
    silent pass (no silent fallback)."""
    return _step(key, NOT_DONE, "Could not be checked: %s" % error)


# -- the steps ------------------------------------------------------------------------

def fiscal_calendar_step(years, period_rows, entity_calendars):
    """``years``: EPM Fiscal Year count. ``period_rows``:
    ``fiscal_calendar.fiscal_period_rows()``. ``entity_calendars``: Entity
    Fiscal Calendar count (an ERP data area mapped to its calendar)."""
    periods = len(period_rows or ())
    open_periods = sum(1 for r in period_rows or () if r.get("status") == "Open")
    mapped = "%s mapped to a calendar (Entity Fiscal Calendar)" % _plural(
        entity_calendars, "ERP data area")
    if not years:
        return _step("fiscal_calendar", NOT_DONE,
                     _sentences(["No EPM Fiscal Year is declared: declare each year and its periods",
                                 mapped]), 0)
    if not periods:
        return _step("fiscal_calendar", PARTIAL,
                     _sentences(["%s declared with no periods" % _plural(years, "fiscal year"), mapped]),
                     years)
    return _step("fiscal_calendar", DONE, _sentences([
        "%s, %s, %d open" % (_plural(years, "fiscal year"), _plural(periods, "period"), open_periods),
        mapped]), years)


def consolidation_group_step(groups, ic_configured):
    """``groups``: one dict per group, from its nodes with no entity (the
    node consolidation reads the reporting currency and the intercompany
    difference account from): ``consolidation_group``, ``reporting_currency``,
    ``ic_difference_account``, ``nci_account`` and ``nodes``, how many such
    nodes it has. ``ic_configured``: an Intercompany Account is Published and
    Close Settings does not declare none, so a difference account is needed.

    A missing Non-controlling Interest Account is reported, never judged:
    ``consolidation_policy_model.required_accounts`` needs it only for an
    acquisition below 100%, and the Business Combination refuses that deal
    itself."""
    groups = list(groups or ())
    if not groups:
        return _step("consolidation_group", NOT_DONE,
                     "No Consolidation Group node without an entity: create the group's own node "
                     "and give it a Reporting Currency.", 0)
    no_currency = sorted(g["consolidation_group"] for g in groups if not g.get("reporting_currency"))
    with_currency = len(groups) - len(no_currency)
    problems = []
    if no_currency:
        problems.append("No Reporting Currency on %s" % _names(no_currency))
    several = sorted("%s (%d nodes without an entity)" % (g["consolidation_group"], g["nodes"])
                     for g in groups if int(g.get("nodes") or 1) > 1)
    if several:
        problems.append("Consolidation reads one node without an entity per group; keep one on %s"
                        % _names(several))
    if ic_configured:
        no_diff = sorted(g["consolidation_group"] for g in groups if not g.get("ic_difference_account"))
        if no_diff:
            problems.append("No Intercompany Difference Account on %s" % _names(no_diff))
    notes = []
    no_nci = sorted(g["consolidation_group"] for g in groups if not g.get("nci_account"))
    if no_nci:
        notes.append("No Non-controlling Interest Account on %s; a Business Combination below 100%% "
                     "needs one" % _names(no_nci))
    if not with_currency:
        state = NOT_DONE
    elif problems:
        state = PARTIAL
    else:
        state = DONE
    head = "%s with a Reporting Currency" % _plural(with_currency, "group")
    return _step("consolidation_group", state, _sentences([head] + problems + notes), len(groups))


def statement_accounts_step(gap_message):
    """``gap_message``: the sign-off gate's statement-account gap
    (``signoff_gate.statement_gap``: the CTA and current-year result
    accounts in Close Settings, and Balance Sheet heading sides), or None."""
    if gap_message:
        return _step("statement_accounts", NOT_DONE, gap_message)
    return _step("statement_accounts", DONE,
                 "The CTA and current-year result accounts are declared in Close Settings.")


def entities_step(entities, grouped, allowed):
    """``entities``: the Active leaf entities as ``{"name",
    "functional_currency"}``. ``grouped``: entity codes some Consolidation
    Group node carries. ``allowed``: the caller's visible entity codes."""
    entities = list(entities or ())
    if not entities:
        return _step("entities", NOT_DONE, "No Active entity: create each entity that reports a trial balance.", 0)
    names = [e["name"] for e in entities]
    ungrouped = [n for n in names if n not in set(grouped or ())]
    no_currency = [e["name"] for e in entities if not e.get("functional_currency")]
    parts = ["%s Active" % _plural(len(entities), "entity", "entities")]
    if ungrouped:
        parts.append("Not in any Consolidation Group: %s" % _entity_list(ungrouped, allowed))
    if no_currency:
        parts.append("No Functional Currency: %s" % _entity_list(no_currency, allowed))
    return _step("entities", PARTIAL if (ungrouped or no_currency) else DONE, _sentences(parts), len(entities))


def chart_step(published_leaves, unresolved_fx, ic_problem):
    """``published_leaves``: Published leaf Main Accounts. ``unresolved_fx``:
    leaf accounts (not Inactive) for which ``group_chart_model.apply_defaults``
    finds no translation method. ``ic_problem``: what
    ``intercompany_account.allow_ic_problem`` says about the accounts the
    Published Intercompany Accounts name ("" when every one allows it)."""
    unresolved_fx = sorted(unresolved_fx or ())
    parts = []
    if unresolved_fx:
        parts.append("No translation method can be resolved for %s: declare Account Type and "
                     "Statement Section, or the Translation Method" % _names(unresolved_fx))
    if ic_problem:
        parts.append(ic_problem)
    if not published_leaves:
        return _step("chart_of_accounts", NOT_DONE, _sentences(
            ["No Published account: upload the group chart and publish it"] + parts), 0,
            route_options={"status": "Draft"})
    head = "%s Published" % _plural(published_leaves, "account")
    return _step("chart_of_accounts", PARTIAL if parts else DONE, _sentences([head] + parts),
                 published_leaves)


def dimensions_step(declared, budget_gated, budget_ungated):
    """``declared``: ``dimension_value_model.declared_dimensions`` (Published,
    in a budget or a trial balance). ``budget_gated`` / ``budget_ungated``:
    the Published budget dimensions with and without a Permission DocType.
    Whether budget entry is restricted is reported, never judged: a site may
    leave every dimension open."""
    declared = sorted(declared or ())
    gating = []
    if budget_gated:
        gating.append("Budget entry is restricted by User Permission on %s" % _names(budget_gated))
    if budget_ungated:
        gating.append("Budget entry is open to every budget user on %s" % _names(budget_ungated))
    if not declared:
        return _step("dimensions", NOT_DONE, _sentences(
            ["No Published Dimension is used in a trial balance or a budget: declare the site's own"]
            + gating), 0)
    return _step("dimensions", DONE, _sentences(
        ["%s declared: %s" % (_plural(len(declared), "dimension"), _names(declared))] + gating),
        len(declared))


def currencies_step(stored, gaps):
    """``stored``: ISO Currency count. ``gaps``:
    ``currency_references.minor_unit_gaps()`` (``{"blocking", "reported"}``),
    the konsol#330 check the sign-off gate reads."""
    if not stored:
        return _step("currencies", NOT_DONE,
                     "No ISO Currency is stored: run bench migrate, which seeds the shipped list.", 0)
    blocking, reported = (gaps or {}).get("blocking"), (gaps or {}).get("reported")
    if blocking:
        return _step("currencies", NOT_DONE, blocking["message"], stored)
    if reported:
        return _step("currencies", PARTIAL, reported["message"], stored)
    return _step("currencies", DONE,
                 "%s, each consistent with the shipped ISO 4217 list" % _plural(stored, "currency", "currencies")
                 + ".", stored)


def intercompany_step(published, ic_state, tolerance_gap, setup_gap):
    """``published``: Published Intercompany Accounts (``ic_api.published_count``).
    ``ic_state``: ``ic_model.state(published, declared_none=...)``, the rule
    every close surface shares: ``not_applicable`` (declared none, nothing
    Published), ``error`` (declared none while pairings are Published),
    ``not_configured``, or a configured state. ``tolerance_gap`` /
    ``setup_gap``: ``ic_api.tolerance_gap()`` and ``ic_api.setup_gap()``, as
    My work shows them."""
    state = ic_state.get("state")
    if state == "not_applicable":
        return _step("intercompany", DONE, "None in this group (declared in Close Settings).", 0)
    if state == "error":
        return _step("intercompany", NOT_DONE, ic_state.get("message") or "", published)
    if not published:
        return _step("intercompany", NOT_DONE, setup_gap or ic_state.get("message") or "", 0)
    head = "%s Published" % _plural(published, "pairing")
    if tolerance_gap:
        message = tolerance_gap.get("message") if isinstance(tolerance_gap, dict) else str(tolerance_gap)
        return _step("intercompany", PARTIAL, _sentences([head, message]), published)
    return _step("intercompany", DONE, _sentences([head]), published)


def users_step(admins, accountants, unassigned, can_assign):
    """``admins``: enabled users holding EPM Admin. ``accountants``: enabled
    users holding Entity Accountant. ``unassigned``: how many of them hold no
    User Permission on Entity (``mywork_api._accountants_without_entities``,
    the My work setup gap). ``can_assign``: the caller holds System Manager,
    the only role Frappe lets open User and User Permission; for anyone else
    the step has no link and says who assigns. Counts only."""
    parts = ["%s, %s" % (_plural(admins, "EPM Admin"), _plural(accountants, "Entity Accountant"))]
    if unassigned:
        parts.append("%s no Entity User Permission"
                     % _plural(unassigned, "Entity Accountant has", "Entity Accountants have"))
    if not admins:
        parts.insert(0, "No enabled user holds EPM Admin")
    if not can_assign and (unassigned or not admins):
        parts.append("Roles and User Permissions are assigned by a System Manager")
    if not admins:
        state = NOT_DONE
    elif unassigned:
        state = PARTIAL
    else:
        state = DONE
    if not can_assign:
        route = None
    elif unassigned:
        route = ["List", "User Permission"]
    else:
        route = ["List", "User"]
    options = {"allow": "Entity"} if (can_assign and unassigned) else None
    return _step("users", state, _sentences(parts), admins, route_options=options, route=route)


def trial_balance_step(submitted, latest_period):
    """``submitted``: submitted Trial Balance Submissions. ``latest_period``:
    the latest period one is submitted for, as ``period_name`` gives it, or None."""
    if not submitted:
        return _step("trial_balance", NOT_DONE, "No trial balance is submitted yet.", 0)
    return _step("trial_balance", DONE, "%s submitted, the latest for %s." % (
        _plural(submitted, "trial balance"), latest_period), submitted)


def rates_step(latest_period, gate, allowed=None):
    """``latest_period``: the latest period with a submitted trial balance, or
    None. ``gate``: ``group_rates.rate_gate`` for it, ``(missing, error,
    blockers)``. An entity named in a blocker is cut to ``allowed``."""
    if latest_period is None:
        return _step("rates", NOT_DONE,
                     "No submitted trial balance yet: the rates are checked for the latest period that has one.")
    missing, error, blockers = gate
    if error:
        return _step("rates", NOT_DONE, "The rates for %s cannot be checked: %s." % (latest_period, error))
    parts, no_currency = [], []
    for blocker in blockers or ():
        m = ENTITY_BLOCKER.match(blocker)
        if m:
            no_currency.append(m.group(1))
        else:
            parts.append(blocker)
    if no_currency:
        parts.append("No Functional Currency: %s" % _entity_list(no_currency, allowed))
    if missing:
        parts.insert(0, "No approved rate for %s: %s" % (
            latest_period, ", ".join("%s → %s %s" % (f, t, rt) for f, t, rt in missing)))
    if parts:
        return _step("rates", NOT_DONE, _sentences(parts), len(missing or ()))
    return _step("rates", DONE, "Every rate %s needs is approved." % latest_period, 0)


def warehouse_step(health, fresh, fresh_error=None):
    """``health``: ``clickhouse.check_health()``. ``fresh``:
    ``freshness_api.current_freshness()`` (its ``state`` and ``as_of``), or
    None when it could not be read, with ``fresh_error`` saying why."""
    if not (health or {}).get("clickhouse_reachable"):
        return _step("warehouse", NOT_DONE, "ClickHouse is not reachable: %s." % (
            (health or {}).get("message") or "no answer"))
    if fresh is None:
        return _step("warehouse", NOT_DONE, "Reachable; the last build could not be read: %s." % (
            fresh_error or "no answer"))
    state, as_of = fresh.get("state"), fresh.get("as_of")
    built = "last build %s" % as_of if as_of else "never built"
    if state == "fresh":
        return _step("warehouse", DONE, "Reachable, %s, numbers current." % built)
    if state == "never_built":
        return _step("warehouse", NOT_DONE, "Reachable, never built: request a full build.")
    why = {
        "stale": "changed since: %s" % ", ".join(fresh.get("changed_since") or ()),
        "pending": "%s waiting" % _plural(int(fresh.get("pending") or 0), "build"),
        "failed": "the last build failed: %s" % ((fresh.get("last_failed") or {}).get("reason") or ""),
    }.get(state, "state %s" % state)
    return _step("warehouse", PARTIAL, "Reachable, %s; %s." % (built, why))


def summary(steps):
    """``{"done", "total", "steps"}``: every step, in ``STEPS`` order."""
    by_key = {s["key"]: s for s in steps}
    missing = [k for k, _, _ in STEPS if k not in by_key]
    if missing:
        raise ValueError("Setup steps missing: %s" % ", ".join(missing))
    ordered = [by_key[k] for k, _, _ in STEPS]
    return {"done": sum(1 for s in ordered if s["state"] == DONE), "total": len(ordered), "steps": ordered}

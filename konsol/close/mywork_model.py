"""My-work setup gaps: one configuration gap is one item. Pure (konsol#305 A14; stories 1.2, 0.4).

Imports nothing from frappe or konsol; the caller gathers ``facts``:

- ``first_close``: the first close period ``(fiscal_year, fiscal_period)``
  from Close Settings, or None. A key with a 0 part is unset (Close Settings
  Int fields read back as 0) and counts as None.
- ``policy_gaps``: the ``close_policy_model.policy_gaps(...)`` list (konsol#305
  P02): ``{"code", "message"}`` for each undeclared policy (self-approval,
  rate move), in that order. ``[]`` when both are declared.
- ``chart_published``: bool; False when ``group_chart.chart_accounts()`` is
  empty.
- ``frequency_missing``: in-scope entities with no ``reporting_frequency``.
- ``ownership_missing``: in-scope leaves with no ownership period covering the
  period (in scope: Active, not a group).
- ``accountants_without_entities``: enabled Entity Accountants with no Entity
  user permission.
- ``ic_accounts_gap``: None, or the help text from ``ic_api.setup_gap()``
  (konsol#305 D2-7; W3-1). The model never writes its own wording: a policy
  gap carries its own message.
- ``ic_tolerance_gap``: None, or ``ic_api.tolerance_gap()``'s
  ``{"code", "groups", "message"}`` (konsol#305 W3-6): the group nodes whose
  intercompany tolerance is undeclared (0 is indistinguishable from unset).

Rules:

- One gap is ONE item that lists everyone affected (#291: never one item per
  entity per month). An empty list gives no item.
- Ids are fixed (``gap:<name>``) and items come in ``GAPS`` order.
- Every item is ``kind "blocking"``, names the owner role that can fix it,
  and its ``action`` opens the Desk page (story 0.4: the Desk only for
  configuration gaps).
- A missing fact raises ValueError: nothing is guessed.
- A53: a gap item has no period, so it carries ``since: None`` with
  ``since_reason: "configuration gap"`` — the screen shows nothing it was
  not sent, never an invented age.
"""

GAPS = ("first_close", "self_approval", "rate_move", "chart", "ic_accounts", "ic_tolerance",
        "frequency", "ownership", "accountants")
FACT_KEYS = ("first_close", "chart_published", "frequency_missing", "ownership_missing",
             "accountants_without_entities", "policy_gaps", "ic_accounts_gap", "ic_tolerance_gap")

#: konsol#305 P02 policy-gap code -> (gap id, title). The message is the gap's own.
_POLICY_GAPS = {
    "self_approval_undeclared": ("self_approval", "Self-approval policy not declared"),
    "rate_move_undeclared": ("rate_move", "Rate move threshold not declared"),
}


def _declared(first_close):
    if first_close is None:
        return False
    year, period = first_close
    return bool(year) and bool(period)


def _entities(n):
    return f"{n} entity" if n == 1 else f"{n} entities"


def _groups(n):
    return f"{n} group" if n == 1 else f"{n} groups"


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
        "since": None,
        "since_reason": "configuration gap",
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
    for gap in facts["policy_gaps"] or ():
        gap_id, title = _POLICY_GAPS[gap["code"]]
        items.append(_item(gap_id, title, gap["message"], "EPM Admin", "/app/close-settings"))
    if not facts["chart_published"]:
        items.append(_item("chart", "Group chart not published",
                           "Publish the Main Accounts of the group chart.", "EPM Admin",
                           "/app/main-account"))
    if facts["ic_accounts_gap"]:
        items.append(_item("ic_accounts", "No intercompany accounts declared",
                           facts["ic_accounts_gap"], "EPM Admin", "/app/intercompany-account"))
    if facts["ic_tolerance_gap"]:
        tol_gap = facts["ic_tolerance_gap"]
        groups = tol_gap["groups"]
        title = "Intercompany tolerance not declared for %s" % _groups(len(groups))
        items.append(_item("ic_tolerance", title, tol_gap["message"], "EPM Admin",
                           "/app/consolidation-group"))
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
# - Close Lead: "Rates missing (N)" and "Re-sign needed" (blocking); "Trial
#   balance with no ownership (N)" (blocking, #289) when the sign-off gate
#   reports a submitted TB with no covering ownership; "Sign off
#   <code>" (todo) when checks are current, none fail, no rates are missing,
#   the period is not signed and no gate blocks. When a gate blocks, the item
#   is "Waiting on <earliest earlier open period>" instead, or "Waiting on the
#   sign-off gates" when no earlier open period explains it. The Close Lead
#   also waits on trial balances and on checks. "Close <code>" (todo) when the
#   period is signed off (``signoff`` in SIGNED_STATES) but its ``status`` is
#   still "Open" (9.3: signing off is not the end of the close, and the
#   period can otherwise sit Open unnoticed, blocking the next one's order
#   gate). Only the Close Lead sees it.
# - Viewer: no items.
# Actions are in-app screens, never the Desk (story 0.4).

CLOSE_LEAD = "close_lead"
GROUP_ACCOUNTANT = "group_accountant"
ENTITY_ACCOUNTANT = "entity_accountant"
VIEWER = "viewer"
PERSONAS = (CLOSE_LEAD, GROUP_ACCOUNTANT, ENTITY_ACCOUNTANT, VIEWER)

PERIOD_KEYS = ("code", "ended", "my_missing", "missing", "checks", "failed", "signoff",
               "gates_blocked", "rates_missing", "status", "unowned")
#: A53: ``since`` (the period's end date, ISO) is read from ``facts`` when the
#: caller supplies it and carried on ``item["period"]["since"]`` for every
#: period item, so B18 can show an age. It is not in PERIOD_KEYS: it is read
#: with ``facts.get``, never guessed or defaulted to today, and a caller that
#: omits it simply gets ``since: None`` on the period (the API layer is the
#: one that requires and validates it, per period row, before it ever reaches
#: this pure model).
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
        "period": {"fiscal_year": fy, "fiscal_period": fp, "code": facts["code"],
                   "since": facts.get("since")},
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


def _rates_item(p, key, facts):
    """konsol#305 E412 (#305-W2-11): the Close Lead and the Group Accountant
    both get "Rates missing (N)", pointed at the Rates screen. One builder,
    called by both personas, so the id/kind/title/action cannot drift apart.
    """
    rates = int(facts["rates_missing"] or 0)
    if not rates:
        return None
    return _period_item(p, key, facts, "rates", "blocking",
                        "Rates missing (%d)" % rates, {"screen": "rates"})


def _group_accountant(key, facts):
    items = []
    p = GROUP_ACCOUNTANT
    rates_item = _rates_item(p, key, facts)
    if rates_item:
        items.append(rates_item)
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
    rates_item = _rates_item(p, key, facts)
    if rates_item:
        items.append(rates_item)
    resign = facts["signoff"] == RE_SIGN_NEEDED
    if resign:
        items.append(_period_item(p, key, facts, "resign", "blocking", "Re-sign needed", signoff))
    unowned = sorted(set(facts["unowned"] or ()))
    if unowned:
        item = _period_item(p, key, facts, "unowned", "blocking",
                            "Trial balance with no ownership (%d)" % len(unowned),
                            {"desk": "/app/ownership-period"})
        item["detail"] = ", ".join(unowned)
        item["entities"] = unowned
        items.append(item)
    signed = facts["signoff"] in SIGNED_STATES
    if signed and facts["status"] == "Open":
        items.append(_period_item(p, key, facts, "close", "todo", "Close %s" % facts["code"],
                                  signoff))
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
    if clean and not resign and not signed:
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


# --- A11: the Close Lead's "waiting for your approval" item -----------------
#
# ``approvals_item(waiting)`` turns A09's ``waiting_for_me`` summary
# (``{"count", "oldest"}``) into one My work item for the Close Lead, or None
# when nothing is waiting. The item carries no period (one item for the whole
# approvals queue, not one per period), so ``rank`` places it first within
# "todo" alongside the setup-gap items.

def approvals_item(waiting):
    if "count" not in waiting:
        raise ValueError("approvals_item: missing count")
    count = int(waiting["count"])
    if not count:
        return None
    oldest = waiting.get("oldest")
    return {
        "id": "approvals",
        "kind": "todo",
        "title": "Approve %d item%s" % (count, "" if count == 1 else "s"),
        "owner": OWNERS[CLOSE_LEAD],
        "action": {"screen": "approvals"},
        "since": oldest[:10] if oldest else None,
        "since_reason": "oldest waiting",
    }


# --- C07 (konsol#305 W3-1, W3-2): the Entity Accountant's IC fix items -------
#
# ``ic_fix_items(fixes_by_key, per_period, allowed)`` turns ic_api's open
# intercompany fixes into one item per (fix, allowed entity of the pair), each
# pointed at Trial balances for that entity (R1: the fix is on the entity's
# own TB). A cleared pair gives no item (ic_api does not return one); an
# uncheckable one stays until it can be judged (E5-P4).
#
# ``fixes_by_key`` is ``{(fiscal_year, fiscal_period): [fix, ...]}``. Each fix
# carries ``entity_a``, ``account_a``, ``entity_b``, ``account_b`` (the pair's
# four-key grain), ``state`` (one of IC_FIX_STATES), ``sent_by``, ``sent_at``
# (ISO datetime) and ``reason`` from the ``ic_sent_back`` event, and, by
# state: ``over_tolerance`` -> ``difference``, ``tolerance``, ``group``,
# ``balance_a``, ``balance_b``; ``cannot_check`` -> ``error``. An unknown
# state raises ValueError: nothing is guessed.
#
# ``per_period`` is mywork_api's per-period facts; only ``code``, ``ended``
# and (optionally) ``since`` are read. A fix whose key is absent from
# ``per_period`` is skipped: that period is not open, so it is not mine to
# show. ``allowed`` is the caller's entity codes (None = both sides, as for
# the EPM Admin and EPM Analyst personas who always see every entity).

IC_FIX_STATES = ("over_tolerance", "not_in_build", "cannot_check")


def _ic_state_sentence(fix, own_balance):
    state = fix["state"]
    if state == "over_tolerance":
        return ("Difference %s in %s (tolerance %s). Your side %s."
                % (fix["difference"], fix["group"], fix["tolerance"], own_balance))
    if state == "not_in_build":
        return ("The pair is not in the last build; this stays until it is within "
                "tolerance or the period closes.")
    if state == "cannot_check":
        return "Intercompany could not be checked (%s); this stays until it can." % fix["error"]
    raise ValueError("ic_fix_items: unknown fix state %r" % (state,))


def _ic_detail(fix, own_balance):
    prefix = "Sent back by %s on %s: %s." % (fix["sent_by"], fix["sent_at"][:10], fix["reason"])
    return prefix + " " + _ic_state_sentence(fix, own_balance)


def _ic_title(partner, own_account, partner_account):
    return "Intercompany difference with %s (%s ↔ %s)" % (partner, own_account, partner_account)


def _ic_fix_item(fy, fp, entity, fix, kind, code, since):
    a, acct_a, b, acct_b = fix["entity_a"], fix["account_a"], fix["entity_b"], fix["account_b"]
    if entity == a:
        partner, own_account, partner_account, own_balance = b, acct_a, acct_b, fix["balance_a"]
    else:
        partner, own_account, partner_account, own_balance = a, acct_b, acct_a, fix["balance_b"]
    # Validate the state before writing any detail (fails closed on an unknown state).
    detail = _ic_detail(fix, own_balance)
    return {
        "id": "ic:%d-%02d:%s:%s|%s|%s|%s" % (fy, fp, entity, a, acct_a, b, acct_b),
        "kind": kind,
        "title": _ic_title(partner, own_account, partner_account),
        "detail": detail,
        "period": {"fiscal_year": fy, "fiscal_period": fp, "code": code, "since": since},
        "owner": "Entity Accountant",
        "action": {"screen": "trial-balances", "entity": entity},
    }


def ic_fix_items(fixes_by_key, per_period, allowed):
    """One item per (fix, allowed entity of the pair); R1, W3-1, W3-2."""
    per_period = per_period or {}
    items = []
    for key, fixes in (fixes_by_key or {}).items():
        if key not in per_period:
            continue
        fy, fp = int(key[0]), int(key[1])
        period_facts = per_period[key]
        kind = "blocking" if period_facts["ended"] else "todo"
        code = period_facts["code"]
        since = period_facts.get("since")
        for fix in fixes or ():
            for entity in (fix["entity_a"], fix["entity_b"]):
                if allowed is not None and entity not in allowed:
                    continue
                items.append(_ic_fix_item(fy, fp, entity, fix, kind, code, since))
    return items


# --- A22: the preparer's "sent back" My work item -----------------------------
#
# ``sent_back_items(rows, persona, period_codes)`` turns A21's
# ``approvals_api.sent_back_for`` rows into one ``todo`` item per sent-back
# draft the caller owns, for every persona except the Viewer (the API never
# asks the Viewer; a Viewer persona here raises). ``rows`` is that function's
# own return shape: ``[{"doctype", "name", "kind_label", "title",
# "fiscal_year", "fiscal_period", "rejection": {"reason", "actor", "at"}}]``.
# ``period_codes`` is ``{(fiscal_year, fiscal_period): period_code}``; a row
# naming a period missing from it raises ValueError — never an invented code.

#: The screen or Desk path that fixes a sent-back draft, by doctype (the
#: engineering call, 3 Oct: Adjustments for a journal, Rates & ownership for
#: GER/HER/OP, the Desk for IC Balance, Business Combination and Business
#: Disposal — the same 3 doctypes A08/A21 treat as Desk-only).
_SENT_BACK_SCREEN = {
    "Consolidation Journal": "adjustments",
    "Group Exchange Rate": "rates",
    "Historical Equity Rate": "rates",
    "Ownership Period": "rates",
}
_SENT_BACK_DESK = {
    "IC Balance": "/app/ic-balance/%s",
    "Business Combination": "/app/business-combination/%s",
    "Business Disposal": "/app/business-disposal/%s",
}
#: Doctypes whose row names a period (the journal, Group Exchange Rate and IC
#: Balance; mirrors approvals_model.sent_back_items).
_SENT_BACK_PERIOD_KEYED = ("Consolidation Journal", "Group Exchange Rate", "IC Balance")


def _sent_back_action(doctype, name):
    if doctype in _SENT_BACK_SCREEN:
        return {"screen": _SENT_BACK_SCREEN[doctype]}
    if doctype in _SENT_BACK_DESK:
        return {"desk": _SENT_BACK_DESK[doctype] % name}
    raise ValueError("sent_back_items: %r is not one of the 7 approval doctypes." % (doctype,))


def sent_back_items(rows, persona, period_codes):
    if persona == VIEWER:
        raise ValueError("sent_back_items: the Viewer has no sent-back item")
    if persona not in PERSONAS:
        raise ValueError("sent_back_items: unknown persona %r" % (persona,))
    owner = OWNERS[persona]

    items = []
    for row in rows or ():
        doctype, name = row["doctype"], row["name"]
        rejection = row["rejection"]
        at = rejection["at"]
        item = {
            "id": "sent-back:%s:%s" % (doctype, name),
            "kind": "todo",
            "title": "Sent back: %s · %s" % (row["kind_label"], row["title"]),
            "detail": "%s on %s: %s" % (rejection["actor"], at[:10], rejection["reason"]),
            "owner": owner,
            "action": _sent_back_action(doctype, name),
        }
        fy, fp = row.get("fiscal_year"), row.get("fiscal_period")
        if doctype in _SENT_BACK_PERIOD_KEYED and fy is not None and fp is not None:
            key = (int(fy), int(fp))
            if key not in period_codes:
                raise ValueError(
                    "sent_back_items: %s %s names period %r, not in period_codes"
                    % (doctype, name, key))
            item["period"] = {"fiscal_year": key[0], "fiscal_period": key[1],
                              "code": period_codes[key], "since": at[:10]}
        else:
            item["since"] = at[:10]
            item["since_reason"] = "sent back"
        items.append(item)
    return items

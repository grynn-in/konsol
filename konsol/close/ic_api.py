"""Intercompany endpoints for the close app (konsol#305 C03; stories 5.1,
5.2; #305-W3-2, W3-6, W3-7; W3 engineering calls: IC reads fail visibly,
and 0 Published Intercompany Accounts is "not configured — nothing was
checked").

``get_ic(fiscal_year, fiscal_period)`` (GET) returns the period's IC state,
the pairs grouped by consolidation group with their send-back state, the
partnerless rows, the counts, the W3-2 mask for a scoped caller and whether
the caller may send back or remind. Each shown pair carries ``reminders_a`` /
``reminders_b`` (topic ic, konsol#305 Y60, C-R6): ``{count, last_at,
last_by, last_by_name}`` or None; a masked side is always None, so a hidden
entity's reminders never leave the server. Each pair also carries
``can_remind_a`` / ``can_remind_b`` (konsol#305 R53b, #305-R52-2-1): true
only when the payload-level ``can_remind`` holds, the side is visible and the
pair is over tolerance (``remind_model.ic_side_can_remind``). Read-only.

``send_back(fiscal_year, fiscal_period, entity_a, account_a, entity_b,
account_b, reason)`` (POST, C04; #305-W3-1 option A) re-reads the pair from
the warehouse and writes one ``ic_sent_back`` Close Event through
``close_event.record`` in the request's transaction. The amounts in the event
come from that read, never from the request; the signature names no other key,
so Frappe drops a forged ``actor``, ``difference``, ``detail`` … Every refusal
comes before the write.

- The state is decided before any warehouse read (``ic_model.state``): a
  Close Settings "None in this group" declaration (W3-7) or 0 Published
  Intercompany Accounts reads nothing from ClickHouse.
- Otherwise ``gold_ic_reconciliation`` and ``gold_ic_unmatched`` are read
  through ``ch_read.rows``. Any failure there is returned as ``error`` /
  ``not_built`` with no rows, never as an empty list.
- A group whose tolerance is 0 has not declared it (W3-6): its pairs are
  shown with ``can_send_back`` False (``ic_model.group_view``, W3-P1).
- Reads: MariaDB fiscal_period_rows 1, Close Settings 1, Intercompany
  Account table_exists + count, Close Event 1, Consolidation Group 1, User
  <= 1; when a pair is shown, one more Close Event read through
  ``close_event.reminders`` (topic ic) and, when a visible side was
  reminded, one more User read (Y60); ClickHouse 2 when configured, 0
  otherwise. Constant in pairs.

``checked_rows(fiscal_year, fiscal_period)`` (R53b) gives remind_api's
topic ic check the period's unmasked gold_ic_reconciliation rows, or the
state's sentence when intercompany was not checked.

``setup_gap()``, ``tolerance_gap()``, ``open_fixes(keys)`` and
``signoff_summary(fiscal_year, fiscal_period)`` (C05) are module-level
helpers, not endpoints: not whitelisted, no ``only_for``, callers gate and
apply scope. ``setup_gap``/``tolerance_gap`` read nothing once intercompany
is configured; ``open_fixes`` and ``signoff_summary`` never raise on a
warehouse failure (``cannot_check`` / ``"error"`` instead).

Import warning: this module imports ``konsol.close.ch_read``,
``close_policy_model`` and ``ic_model`` at module level. Test loaders that
build ``konsol.close`` as a stub package must stub ``konsol.close.ic_api``
(C06t, C09t, C18t). ``close_event`` is imported only lazily by ``send_back``
(C04) and by ``get_ic``'s reminders read (Y60). ``remind_model`` is loaded
by path.
"""
import json
from datetime import date, datetime

import frappe

from konsol import fiscal_calendar
from konsol.close import ch_read, close_policy_model, ic_model
from konsol.close.timefmt import zoned_iso
from konsol.entity_permissions import allowed_entity_codes

import importlib.util as _importlib_util
import os as _os


def _load_by_path(filename, module_name):
    """A pure sibling module loaded by path, reachable even under the host
    tests' stub ``konsol.close`` package."""
    path = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), filename)
    spec = _importlib_util.spec_from_file_location(module_name, path)
    module = _importlib_util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_period_name():
    """konsol/close/period_name.py loaded by path (konsol#305 review-w5): the
    one "FY2025 P07" format."""
    return _load_by_path("period_name.py", "konsol_close_period_name").period_name


period_name = _load_period_name()
#: The Remind rules (konsol#305 Y52), pure: REMIND_ROLES and the summary.
remind_model = _load_by_path("remind_model.py", "konsol_close_remind_model")

#: Who reads the Intercompany screen (the literal in ``get_ic``'s gate; the
#: contract test reads it there). The Entity Accountant reads only their fix
#: items, through My work.
IC_ROLES = ("EPM Admin", "EPM Analyst", "EPM User", "System Manager")
#: Who may send a pair back (C04's roles).
SEND_BACK_ROLES = ("EPM Analyst", "EPM Admin", "System Manager")

IC_ACCOUNT = "Intercompany Account"

#: A blank Link is stored as NULL: match both spellings (the group node is
#: the Consolidation Group row with no entity).
_BLANK = ["is", "not set"]

_RECONCILIATION_SQL = (
    "SELECT consolidation_group, fiscal_year, fiscal_period, entity_a, account_a, "
    "entity_b, account_b, basis, pair_event, currency_a, currency_b, local_a, local_b, "
    "balance_a, balance_b, group_balance_a, group_balance_b, share_a, share_b, "
    "matched_amount, difference, net_balance, residual_a, residual_b, difference_cause, "
    "ic_difference_account, tolerance, match_status "
    "FROM epm_gold.gold_ic_reconciliation "
    "WHERE fiscal_year = {fy:UInt16} AND fiscal_period = {fp:UInt16}"
)
#: send_back's read: one pair in one period, every consolidation group.
_PAIR_SQL = (
    "SELECT consolidation_group, entity_a, account_a, entity_b, account_b, difference, "
    "tolerance, match_status "
    "FROM epm_gold.gold_ic_reconciliation "
    "WHERE fiscal_year = {fy:UInt16} AND fiscal_period = {fp:UInt16} "
    "AND entity_a = {ea:String} AND account_a = {aa:String} "
    "AND entity_b = {eb:String} AND account_b = {ab:String} "
    "ORDER BY consolidation_group"
)
_UNMATCHED_SQL = (
    "SELECT consolidation_group, data_area_id, main_account, counterpart_account, "
    "unmatched_local_amount, unmatched_amount "
    "FROM epm_gold.gold_ic_unmatched "
    "WHERE fiscal_year = {fy:UInt16} AND fiscal_period = {fp:UInt16}"
)

#: gold_ic_reconciliation's numeric columns. FORMAT JSON may quote them
#: (64-bit and Decimal values, E5-P15), so each is coerced with float().
_PAIR_NUMBERS = ("local_a", "local_b", "balance_a", "balance_b", "group_balance_a",
                 "group_balance_b", "share_a", "share_b", "matched_amount", "difference",
                 "net_balance", "residual_a", "residual_b", "tolerance")
_UNMATCHED_NUMBERS = ("unmatched_local_amount", "unmatched_amount")


def _period(fiscal_year, fiscal_period):
    try:
        return int(fiscal_year), int(fiscal_period)
    except (TypeError, ValueError):
        frappe.throw(f"Fiscal year {fiscal_year!r}, period {fiscal_period!r} is not a period: "
                     "pass the fiscal year and period as whole numbers.")


def _period_row(key):
    for row in fiscal_calendar.fiscal_period_rows():
        if (int(row["fiscal_year"]), int(row["fiscal_period"])) == key:
            return row
    frappe.throw("%s is not declared: declare it in EPM Fiscal Year." % period_name(*key))


def _iso(value):
    """A datetime with the site's UTC offset; a plain date stays a date."""
    if isinstance(value, datetime):
        return zoned_iso(value, frappe.utils.get_system_timezone())
    if isinstance(value, date):
        return value.isoformat()
    return None if value in (None, "") else str(value)


def _numbers(row, columns):
    out = dict(row)
    for col in columns:
        if out.get(col) is not None:
            out[col] = float(out[col])
    for col in ("fiscal_year", "fiscal_period"):
        if out.get(col) is not None:
            out[col] = int(out[col])
    return out


def published_count():
    """Count of Published Intercompany Accounts; 0 on a site the doctype has
    not reached yet (as intercompany_account.intercompany_accounts guards).
    C05 reuses it."""
    if not frappe.db.table_exists(IC_ACCOUNT):
        return 0
    return int(frappe.db.count(IC_ACCOUNT, {"status": "Published"}))


def declared_none():
    """Close Settings declares "None in this group" (W3-7). Needs the Close
    Settings meta (W3-P9): its error is never caught. C05 reuses it."""
    value = frappe.db.get_single_value("Close Settings", "intercompany_declaration")
    return value == close_policy_model.INTERCOMPANY_NONE


def setup_gap():
    """C05: My work's "no intercompany accounts declared" setup gap (D2-7;
    story 1.2). None once intercompany is configured (``published_count() >
    0``) or Close Settings declares "none in this group" (W3-7): there is
    then nothing to declare. Not whitelisted: the caller (My work) gates."""
    if declared_none():
        return None
    if published_count() == 0:
        return ic_model.SETUP_HELP
    return None


def tolerance_gap():
    """C05: My work's W3-6 setup gap — a consolidation group node that has
    not declared its intercompany difference tolerance (0 is undeclared).
    None while intercompany is not configured or declared not applicable
    (W3-P2): there is nothing yet to judge a tolerance against. Group nodes
    are the Consolidation Group rows with no entity (W3-P13), the same
    filter as ``_currencies``. Not whitelisted: the caller gates."""
    published = published_count()
    none = declared_none()
    if published == 0 or none:
        return None
    groups = frappe.get_all(
        "Consolidation Group", filters={"data_area_id": _BLANK},
        fields=["consolidation_group", "ic_difference_tolerance"],
        limit_page_length=0)
    return ic_model.tolerance_gap(published, none, groups)


def open_fixes(keys):
    """C05: My work's open intercompany fix items, per open period (D2-7,
    E5-P2, E5-P4). ``keys`` is a list of ``(fiscal_year, fiscal_period)`` —
    My work's open periods. Returns ``{key: [fix, …]}`` (``ic_model.
    open_fixes`` shape, unmasked); a key with no open fix is omitted. Not
    whitelisted: the caller gates and applies scope."""
    keys = [(int(fy), int(fp)) for fy, fp in keys]
    if not keys or published_count() == 0 or declared_none():
        return {}
    key_set = set(keys)
    years = sorted({fy for fy, _fp in keys})
    events = frappe.get_all(
        "Close Event",
        filters={"kind": "ic_sent_back", "fiscal_year": ["in", years]},
        fields=["name", "at", "actor", "reason", "detail", "fiscal_year", "fiscal_period"],
        limit_page_length=0,
    )
    by_key = {}
    for event in events:
        key = (int(event["fiscal_year"]), int(event["fiscal_period"]))
        if key not in key_set:
            continue
        event = dict(event)
        event["kind"] = "ic_sent_back"
        detail = event.get("detail")
        event["detail"] = json.loads(detail) if isinstance(detail, str) and detail else (
            detail or {})
        by_key.setdefault(key, []).append(event)

    out = {}
    for key in keys:
        key_events = by_key.get(key)
        if not key_events:
            continue
        fy, fp = key
        try:
            rows = [_numbers(r, _PAIR_NUMBERS)
                    for r in ch_read.rows(_RECONCILIATION_SQL, {"fy": fy, "fp": fp})]
            error = None
        except Exception as e:  # noqa: BLE001 — any failure to read means "can't say"
            rows, error = [], _error_text(e)
        fixes = ic_model.open_fixes(key_events, rows, error=error)
        if fixes:
            # S1: sent_at leaves ic_model as a raw datetime; zone it here (the
            # caller's My work detail slices it as an ISO string).
            out[key] = [dict(fix, sent_at=_iso(fix["sent_at"])) for fix in fixes]
    return out


def signoff_summary(fiscal_year, fiscal_period):
    """C05: the sign-off summary's intercompany line (stories 9.x; E5-P13: a
    count only, unscoped — never an entity or an amount). A warehouse
    failure, or "not configured" / "not applicable" / the W3-7 conflict,
    reads as its own state with ``counts`` and ``sent_back_open`` None,
    never a guessed 0 (``ic_model.counts`` still raises on an unknown
    ``match_status``: S9). Not whitelisted: the caller gates."""
    fy, fp = int(fiscal_year), int(fiscal_period)
    published = published_count()
    none = declared_none()
    result = ic_model.state(published, declared_none=none)
    if result["state"] == "checked":
        rows, unmatched, error = _warehouse(fy, fp)
        if error:
            result = ic_model.state(published, error, declared_none=none)
        else:
            events = _sent_back_events(fy, fp)
            return ic_model.signoff_line("checked", rows, unmatched, events)
    return {"state": result["state"], "message": result["message"], "counts": None,
            "sent_back_open": None}


def checked_rows(fiscal_year, fiscal_period):
    """konsol#305 R53b: ``(rows, None)`` with the period's
    gold_ic_reconciliation rows (every group, unmasked), or ``([], message)``
    when intercompany was not checked: not configured, not applicable, the
    W3-7 conflict (all decided before any warehouse read, as ``get_ic``), or
    a warehouse failure. Never an empty list for "could not read". Not
    whitelisted: the caller (remind_api) gates and applies scope."""
    fy, fp = int(fiscal_year), int(fiscal_period)
    published = published_count()
    none = declared_none()
    result = ic_model.state(published, declared_none=none)
    if result["state"] != "checked":
        return [], result["message"]
    try:
        rows = [_numbers(r, _PAIR_NUMBERS)
                for r in ch_read.rows(_RECONCILIATION_SQL, {"fy": fy, "fp": fp})]
    except Exception as e:  # noqa: BLE001 — any failure to read means "can't say"
        error = {"not_built": ch_read.not_built(e), "text": _error_text(e)}
        return [], ic_model.state(published, error, declared_none=none)["message"]
    return rows, None


def _warehouse(fy, fp):
    """``(rows, unmatched, error)``; on any failure no rows and the error."""
    params = {"fy": fy, "fp": fp}
    try:
        rows = [_numbers(r, _PAIR_NUMBERS) for r in ch_read.rows(_RECONCILIATION_SQL, params)]
        unmatched = [_numbers(r, _UNMATCHED_NUMBERS)
                     for r in ch_read.rows(_UNMATCHED_SQL, params)]
    except Exception as e:  # noqa: BLE001 — any failure to read means "can't say"
        return [], [], {"not_built": ch_read.not_built(e), "text": _error_text(e)}
    return rows, unmatched, None


def _sent_back_events(fy, fp):
    events = frappe.get_all(
        "Close Event",
        filters={"kind": "ic_sent_back", "fiscal_year": fy, "fiscal_period": fp},
        fields=["name", "at", "actor", "reason", "detail"],
        limit_page_length=0,
    )
    out = []
    for event in events:
        event = dict(event)
        event["kind"] = "ic_sent_back"
        detail = event.get("detail")
        event["detail"] = json.loads(detail) if isinstance(detail, str) and detail else (
            detail or {})
        out.append(event)
    return out


def _currencies():
    rows = frappe.get_all("Consolidation Group", filters={"data_area_id": _BLANK},
                          fields=["consolidation_group", "reporting_currency"],
                          limit_page_length=0)
    return {r["consolidation_group"]: r.get("reporting_currency") or None for r in rows}


def _user_names(events):
    actors = sorted({e.get("actor") for e in events if e.get("actor")})
    if not actors:
        return {}
    rows = frappe.get_all("User", filters={"name": ["in", actors]},
                          fields=["name", "full_name"], limit_page_length=0)
    return {r["name"]: r.get("full_name") or r["name"] for r in rows}


def _sent_back_out(entry, users):
    if entry is None:
        return None
    by = entry.get("by")
    return {"by": by, "by_name": users.get(by, by), "at": _iso(entry.get("at")),
            "reason": entry.get("reason")}


def _reminders(key, visible):
    """``{entity: {count, last_at, last_by, last_by_name}}`` for the
    ``visible`` entities (konsol#305 Y60, C-R6, as grid_api ``_reminders``):
    one ``close_event.reminders`` read for the period and topic ic,
    summarised by ``remind_model.summary``. Only visible entities' entries are
    kept, so a hidden entity's reminders (and who sent them) never leave the
    server. An unreadable event raises (``summary``): a count is never
    guessed as 0. The sender is named by ``remind_model.sender_name``: the
    full name, or the labelled id "<id> (name not recorded)" when the user
    has no full name or no longer exists, so one bad sender never takes down
    the IC screen (konsol#305 R52d, review-w5b S3)."""
    from konsol.close import close_event  # lazy: see the import warning above

    summary = remind_model.summary(close_event.reminders([key], "ic"))
    entries = remind_model.visible_entries(summary, [key], "ic", visible)[key]
    if not entries:
        return {}
    actors = sorted({e["last_by"] for e in entries.values()})
    names = {u["name"]: u.get("full_name") for u in frappe.get_all(
        "User", filters={"name": ["in", actors]}, fields=["name", "full_name"],
        limit_page_length=0)}
    return {entity: {"count": int(e["count"]), "last_at": _iso(e["last_at"]),
                     "last_by": e["last_by"],
                     "last_by_name": remind_model.sender_name(e["last_by"], names)}
            for entity, e in entries.items()}


@frappe.whitelist(methods=["GET"])
def get_ic(fiscal_year, fiscal_period):
    """The period's intercompany read: ``{"period", "state", "message",
    "help", "published", "declared_none", "groups", "unmatched", "counts",
    "hidden", "can_send_back", "can_remind"}``. Read-only. Fixed shape for
    C12/C14; each pair also carries ``reminders_a`` / ``reminders_b`` (Y60)."""
    frappe.only_for(("EPM Admin", "EPM Analyst", "EPM User", "System Manager"))
    key = _period(fiscal_year, fiscal_period)
    row = _period_row(key)
    fy, fp = key

    none = declared_none()
    published = published_count()
    result = ic_model.state(published, declared_none=none)
    groups, unmatched, counts = [], [], None
    hidden = {"pairs": 0, "unmatched": 0}

    if result["state"] == "checked":
        rows, unmatched_rows, error = _warehouse(fy, fp)
        if error:
            result = ic_model.state(published, error, declared_none=none)
        else:
            events = _sent_back_events(fy, fp)
            allowed = allowed_entity_codes()
            shown, hidden_pairs = ic_model.mask(rows, allowed)
            unmatched, hidden_unmatched = ic_model.mask_unmatched(unmatched_rows, allowed)
            hidden = {"pairs": hidden_pairs, "unmatched": hidden_unmatched}
            counts = ic_model.counts(shown, unmatched)
            groups = ic_model.group_view(shown, events, _currencies())
            users = _user_names(events)
            visible = set()
            for group in groups:
                for pair in group["pairs"]:
                    if not pair["masked_a"]:
                        visible.add(pair["entity_a"])
                    if not pair["masked_b"]:
                        visible.add(pair["entity_b"])
            reminded = _reminders(key, visible) if visible else {}
            for group in groups:
                for pair in group["pairs"]:
                    pair["sent_back"] = _sent_back_out(pair["sent_back"], users)
                    # W3-2: a masked side never carries its reminders.
                    pair["reminders_a"] = (None if pair["masked_a"]
                                           else reminded.get(pair["entity_a"]))
                    pair["reminders_b"] = (None if pair["masked_b"]
                                           else reminded.get(pair["entity_b"]))

    status = row.get("status")
    roles = set(frappe.get_roles())
    can_send_back = bool(roles.intersection(SEND_BACK_ROLES)) and status == "Open" \
        and result["state"] == "checked"
    # C-R1: Remind on the IC panel, as can_send_back (role, Open, checked).
    can_remind = bool(roles.intersection(remind_model.REMIND_ROLES)) and status == "Open" \
        and result["state"] == "checked"
    # R53b (#305-R52-2-1): per side, only a visible side of a pair over
    # tolerance; the same rule remind_api's topic ic refusal uses.
    for group in groups:
        for pair in group["pairs"]:
            pair["can_remind_a"] = remind_model.ic_side_can_remind(
                can_remind, pair["masked_a"], pair)
            pair["can_remind_b"] = remind_model.ic_side_can_remind(
                can_remind, pair["masked_b"], pair)

    return {
        "period": {"fiscal_year": fy, "fiscal_period": fp,
                   "period_code": row.get("period_code"), "status": status},
        "state": result["state"],
        "message": result["message"],
        "help": ic_model.SETUP_HELP if result["state"] == "not_configured" else None,
        "published": published,
        "declared_none": none,
        "groups": groups,
        "unmatched": unmatched,
        "counts": counts,
        "hidden": hidden,
        "can_send_back": can_send_back,
        "can_remind": can_remind,
    }


def _error_text(e):
    names = sorted(ch_read.error_names(e))
    return type(e).__name__ + (f" {', '.join(names)}" if names else "")


@frappe.whitelist(methods=["POST"])
def send_back(fiscal_year, fiscal_period, entity_a, account_a, entity_b, account_b, reason):
    """Send an over-tolerance pair back to both entities (#305-W3-1): one
    ``ic_sent_back`` Close Event, side A as its entity (E5-P8). Returns
    ``{"event", "fix_items_for"}``. No **kwargs: a forged key never arrives."""
    frappe.only_for(("EPM Analyst", "EPM Admin", "System Manager"))
    reason = (reason or "").strip()
    if not reason:
        frappe.throw("Say why the difference is sent back: the entities read this reason.")
    key = _period(fiscal_year, fiscal_period)
    row = _period_row(key)
    fy, fp = key
    status = row.get("status")
    if status != "Open":
        frappe.throw("%s is %s: send a difference back only in an Open period."
                     % (period_name(fy, fp), status))
    if declared_none():
        frappe.throw("Close Settings declares no intercompany in this group: "
                     "nothing can be sent back.")
    published = published_count()
    if published == 0:
        frappe.throw(ic_model.NOT_CONFIGURED + " Nothing can be sent back.")
    allowed = allowed_entity_codes()
    if allowed is not None and entity_a not in allowed and entity_b not in allowed:
        frappe.throw("You can see neither entity of this pair.")

    params = {"fy": fy, "fp": fp, "ea": entity_a, "aa": account_a, "eb": entity_b,
              "ab": account_b}
    try:
        rows = [_numbers(r, ("difference", "tolerance")) for r in ch_read.rows(_PAIR_SQL, params)]
    except Exception as e:  # noqa: BLE001 — any failure to read means "can't say"
        error = {"not_built": ch_read.not_built(e), "text": _error_text(e)}
        frappe.throw(ic_model.state(published, error)["message"] + " Nothing was sent back.")

    pair = "%s %s ↔ %s %s" % (entity_a, account_a, entity_b, account_b)
    if not rows:
        frappe.throw("%s is not an intercompany pair in %s's last build." % (pair, period_name(fy, fp)))
    over = [r for r in rows if r.get("match_status") == "over_tolerance"]
    if not over:
        statuses = ", ".join(sorted({str(r.get("match_status")) for r in rows}))
        frappe.throw("Only a difference over tolerance can be sent back; this pair is %s."
                     % statuses)
    if all(float(r.get("tolerance") or 0) <= 0 for r in over):
        groups = sorted({r["consolidation_group"] for r in over})
        frappe.throw("%s ha%s not declared an intercompany difference tolerance "
                     "(0 is undeclared): declare it on the Consolidation Group before sending "
                     "a difference back." % (", ".join(groups), "s" if len(groups) == 1 else "ve"))

    from konsol.close import close_event  # lazy: see the import warning above

    name = close_event.record(
        "ic_sent_back", fy, fp, reason=reason, entity=entity_a,
        detail={"entity_a": entity_a, "account_a": account_a, "entity_b": entity_b,
                "account_b": account_b,
                "groups": [{"consolidation_group": r["consolidation_group"],
                            "difference": r.get("difference"),
                            "tolerance": r.get("tolerance"),
                            "match_status": r.get("match_status")} for r in rows]})
    return {"event": name, "fix_items_for": [entity_a, entity_b]}

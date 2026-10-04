"""Sign-off gate readers, frappe-bound (konsol#305 A17; story 9.2, #303-3a).

Reads the site and passes it through the pure models:

- ``in_scope_entities(fy, fp)``: the entities a period's close covers. Mirrors
  the old home screen's context reader: an Active leaf Entity (``is_group=0``) with a
  submitted Ownership Period (``data_area_id`` set) whose ``effective_date``
  is on or before the period start and whose ``end_date`` is blank or on or
  after it. Connector-fed entities are NOT exempt (Problems 7,
  konsolidat#221): they owe a TB or a declared TB Exception like any other.
- ``sign_off_problems(fy, fp)`` -> ``{config_gaps, order, completeness}``.
  ``config_gaps`` is checked first; while ``first_close_undeclared`` stands
  the order gate is skipped (``order_problem`` needs a declared first close).
  Only Regular periods are gated (P5): a non-Regular target is refused, and
  Opening/Closing/Adjustment rows never block the order gate.
  A submitted TB from an entity with no covering ownership at the period
  start is a ``tb_without_ownership`` config gap (E205b, #289, #305-W2-2).
- ``assert_can_sign(fy, fp)`` throws one message listing every problem,
  titled "Sign-off blocked".
- ``assert_period_closable(fy, fp, period_type)`` (A23; story 9.3): closing
  or locking an Open Regular period at or after the first close period needs
  a signed run (``assertion_run.assert_close_signed_off``). History periods
  and non-Regular periods are exempt (P5); an undeclared first close refuses.

- ``mark_resign_needed_on_reopen(fy, fp, code, reason, user)`` (A31, A57;
  #303 point 4): reopening a period marks the latest signed run of the
  reopened period itself and of every later Regular period "Re-sign Needed",
  with ``affected_by`` naming the reopen, so the reopened period cannot close
  again on its old signature. Periods before the first close (history) are
  never marked; with no first close declared, history cannot be told apart, so
  every signed Regular run from the reopened period on is marked (the mark
  errs toward re-signing). The mark is saved through
  ``assertion_run.writing(SIGNOFF_WRITER, run)``, so the frozen-field guard
  (A48) still applies to everything else; it never uses ``db.set_value``.
- ``record_data_change(fy, fp, text, user, entity=None)`` (A63, #305-R2b-3;
  entity: S1, E2-6): a trial balance or TB exception submitted or cancelled,
  or an amount basis set, changes the data a period's checks read. The
  period row's ``data_changed_at`` / ``data_changed_by`` / ``data_change``
  are set (a direct row update: no EPM Fiscal Year validate runs); every
  LATER declared Regular period's own row is also stamped, with a carried
  marker naming the source period, UNLESS that row already carries its own
  newer change (R41k, review-w4-server.md S2 effect 3 — ``_stamp_carried_change``);
  and the changed period's AND every later Regular period's latest signed
  run is marked "Re-sign Needed" through the same writer, with
  ``affected_by`` = "<text> at <time> by <user>". History periods (before
  the first close) and non-Regular periods are recorded but never marked or
  carried. ``entity``, when the caller names one, is passed to the
  ``signoff_voided`` Close Event the mark writes, so trail scoping hides a
  void whose reason names a hidden TB. ``sign_off_close`` refuses a run that
  did not start after ``data_changed_at`` (A65), through
  ``signoff_model.data_change_problem`` (A66) — which now also blocks an
  UNSIGNED later period's stale check run, not only a signed one.
- ``data_change(fy, fp)``: the period row's three fields, blanks as None.
- ``statement_accounts()`` / ``statement_gap()`` (#305-W4-1 1c, N45): the
  declared CTA account and current-year result account, read from Close
  Settings and resolved through ``close_policy_model.statement_accounts``;
  plus, since N45b (#305-W4-2 2a-ii, coordinator call W4-E22), every
  Published Balance Sheet heading whose ``normal_balance`` side is
  undeclared, found through ``statement_model._bs_heading_sides`` — the
  same rule the statement itself and the drill use, never re-derived here.
  Both checks share one Main Account read (every row, not only the two
  declared codes: the heading-side check must run whatever the CTA/result
  state is). Either problem lands in the SAME gap
  (``close_policy_model.STATEMENT_ACCOUNTS_UNDECLARED``), so My work and
  readiness need no new gap label — they already show this one.
  ``sign_off_problems`` appends ``statement_gap()`` to ``config_gaps``, right
  after the two policy gaps and before the IC tolerance gap, when either
  statement account is undeclared or unusable, or a BS heading's side is
  undeclared (never defaulted).
- ``sign_off_problems`` also appends ``ic_api.tolerance_gap()`` (C05) to
  ``config_gaps``, right after the two policy gaps and the statement gap,
  when a consolidation
  group node has not declared its intercompany difference tolerance
  (#305-W3-6; W3-P2). It costs 1-3 extra MariaDB reads per call (none once
  intercompany is not configured or declared not applicable), the same
  shape as ``_policies``; My work calls this gate once per open period, so
  the same multiple applies there.
- ``intercompany(fy, fp)`` -> ``ic_api.signoff_summary(fy, fp)``: the IC line
  for the sign-off signature (#305-W3-8). It never raises for a warehouse
  failure; a read failure comes back as its own ``"error"`` / ``"not_built"``
  state.

The first close period is read from Close Settings; its Int fields read back
as 0 when unset, which ``signoff_model.first_close_key`` maps to undeclared.
No default is guessed. Nothing here is whitelisted.
"""
import datetime

import frappe

from konsol import fiscal_calendar
from konsol.close import (
    close_policy_model, ic_api, period_model, scope_model, signoff_model, statement_model)
from konsol.period_status import PeriodNotDeclared

BLOCKED_TITLE = "Sign-off blocked"
CLOSE_BLOCKED_TITLE = "Close blocked"
REGULAR = "Regular"


def _date(value):
    if isinstance(value, datetime.datetime):
        return value.date()
    if isinstance(value, datetime.date):
        return value
    if isinstance(value, str) and value:
        return datetime.date.fromisoformat(value[:10])
    return None


def _key(fiscal_year, fiscal_period):
    return (int(fiscal_year), int(fiscal_period))


def _row(rows, key):
    for row in rows:
        if _key(row["fiscal_year"], row["fiscal_period"]) == key:
            return row
    frappe.throw(
        "FY%d P%02d is not declared: create it in EPM Fiscal Year." % key, PeriodNotDeclared
    )


def _regular_row(rows, key):
    row = _row(rows, key)
    if row.get("period_type") != REGULAR:
        frappe.throw(
            "FY%d P%02d is a %s period; only Regular periods are signed off."
            % (key[0], key[1], row.get("period_type") or "blank-type")
        )
    return row


def _scope(start):
    """``({entity: reporting_frequency}, covered_set)`` for the entities in
    scope at ``start``. The coverage rule itself lives in ``scope_model``
    (G02): both callers here, and ``sign_off_problems``'s #289 caller (E205b),
    read it from one place."""
    entities = frappe.get_all(
        "Entity", filters={"is_group": 0, "status": "Active"},
        fields=["name", "reporting_frequency"], limit_page_length=0,
    )
    names = {e["name"]: e["reporting_frequency"] or "" for e in entities}
    rows = frappe.get_all(
        "Ownership Period",
        filters={"docstatus": 1, "effective_date": ["<=", start], "data_area_id": ["is", "set"]},
        fields=["data_area_id", "end_date", "effective_date"], limit_page_length=0,
    )
    covered = scope_model.covered(rows, start)
    frequencies = {e: names[e] for e in scope_model.in_scope(names, covered)}
    return frequencies, covered


def in_scope_entities(fiscal_year, fiscal_period):
    """Names of the entities in scope for the period, sorted."""
    row = _row(fiscal_calendar.fiscal_period_rows(), _key(fiscal_year, fiscal_period))
    return sorted(_scope(_date(row["start_date"]))[0])


def _first_close():
    return signoff_model.first_close_key((
        frappe.db.get_single_value("Close Settings", "first_close_fiscal_year"),
        frappe.db.get_single_value("Close Settings", "first_close_fiscal_period"),
    ))


def _policies():
    """(self_approval, rate_move_threshold), read straight from Close
    Settings; undeclared (blank / 0) is never guessed (P05, #305-D2-3,
    #305-D2-9)."""
    return (
        frappe.db.get_single_value("Close Settings", "self_approval"),
        frappe.db.get_single_value("Close Settings", "rate_move_threshold"),
    )


def _undeclared_bs_heading_sides(accounts):
    """Every Published Balance Sheet heading in ``accounts`` (``{code:
    {"is_group", "status", "statement_section", "normal_balance", ...}}``)
    whose ``normal_balance`` is blank, or None when every one is declared.
    Reuses ``statement_model._bs_heading_sides`` (N45b, #305-W4-2 2a-ii) —
    the same rule the statement and the drill use for a BS heading's side —
    rather than re-checking ``normal_balance`` here."""
    bs_headings = sorted(
        code for code, row in accounts.items()
        if row.get("is_group") and row.get("status") == "Published"
        and row.get("statement_section") == statement_model.BS
    )
    if not bs_headings:
        return None
    try:
        statement_model._bs_heading_sides(bs_headings, accounts)
    except ValueError as exc:
        return str(exc)
    return None


def statement_accounts():
    """The declared CTA account and current-year result account
    (konsol#305-W4-1 1c), read from Close Settings, resolved against their
    Main Account rows through ``close_policy_model.statement_accounts``
    (N41's rule; never defaulted); plus (N45b) every Published Balance Sheet
    heading whose ``normal_balance`` side is undeclared
    (``_undeclared_bs_heading_sides``). One Main Account read — every row,
    not filtered to the two declared codes, since the heading-side check
    must run whatever the CTA/result state is — serves both checks. A
    heading-side problem lands in the SAME gap as an undeclared/invalid
    CTA or result account (``close_policy_model.STATEMENT_ACCOUNTS_UNDECLARED``):
    one statement setup gap, not two. N51 reuses this reader."""
    cta_account = frappe.db.get_single_value("Close Settings", "statement_cta_account")
    result_account = frappe.db.get_single_value("Close Settings", "statement_result_account")
    accounts = {
        r["name"]: r
        for r in frappe.get_all(
            "Main Account",
            fields=["name", "is_group", "status", "statement_section", "account_name",
                    "normal_balance", "parent_account"],
            limit_page_length=0,
        )
    }
    codes = [c for c in (cta_account, result_account) if c]
    rows = {code: accounts[code] for code in codes if code in accounts}
    declared = close_policy_model.statement_accounts(cta_account, result_account, rows)
    heading_problem = _undeclared_bs_heading_sides(accounts)
    if heading_problem:
        gap = declared["gap"]
        if gap is None:
            gap = {"code": close_policy_model.STATEMENT_ACCOUNTS_UNDECLARED,
                   "message": "", "problems": []}
            declared["gap"] = gap
        gap["problems"].append(heading_problem)
        gap["message"] = " ".join(gap["problems"])
    return declared


def statement_gap():
    """The one setup gap from ``statement_accounts()``, or None when both
    statement accounts are declared and usable."""
    return statement_accounts()["gap"]


def _latest_runs():
    """The latest terminal Assertion Run per period (mirrors assertion_run.latest_close_run)."""
    # Imported here: assertion_run's sign-off will call this gate (A22).
    from konsol.consolidation.doctype.assertion_run.assertion_run import TERMINAL_STATUSES

    runs = {}
    for r in frappe.get_all(
        "Assertion Run",
        filters={"status": ["in", list(TERMINAL_STATUSES)]},
        fields=["name", "fiscal_year", "fiscal_period", "status", "signoff_status", "completed_at"],
        order_by="completed_at desc, creation desc", limit_page_length=0,
    ):
        runs.setdefault(_key(r["fiscal_year"], r["fiscal_period"]), r)
    return runs


def _submitted(doctype, key):
    return frappe.get_all(
        doctype,
        filters={"fiscal_year": key[0], "fiscal_period": key[1], "docstatus": 1},
        fields=["data_area_id", "docstatus"], limit_page_length=0,
    )


def sign_off_problems(fiscal_year, fiscal_period):
    """``{"config_gaps": [...], "order": {...}|None, "completeness": {...}|None}``."""
    key = _key(fiscal_year, fiscal_period)
    rows = fiscal_calendar.fiscal_period_rows()
    row = _regular_row(rows, key)
    frequencies, covered = _scope(_date(row["start_date"]))
    first = _first_close()

    gaps = signoff_model.config_gaps(first, key, frequencies)

    order = None
    if first is not None:
        regular = [r for r in rows if r.get("period_type") == REGULAR]
        # loaded_keys only feeds the catch-up label, which the gate does not read.
        states = period_model.period_states(regular, _latest_runs(), first, ())["states"]
        order = signoff_model.order_problem(states, first, key)

    expected = signoff_model.expected_entities(frequencies, key, rows)
    gaps.extend(expected["gaps"])
    gaps.extend(close_policy_model.policy_gaps(*_policies()))
    statement_problem = statement_gap()
    if statement_problem:
        gaps.append(statement_problem)
    tolerance = ic_api.tolerance_gap()
    if tolerance:
        gaps.append(tolerance)
    tbs = _submitted("Trial Balance Submission", key)
    # #289 (#305-W2-2): a submitted TB from an entity with no covering
    # ownership at the period start is consolidated nowhere; it blocks.
    unowned = scope_model.uncovered_with_tb({r["data_area_id"] for r in tbs}, covered)
    unowned_gap = signoff_model.unowned_tb_gap(sorted(unowned), key)
    if unowned_gap:
        gaps.append(unowned_gap)
    completeness = signoff_model.completeness_problem(
        expected["expected"], tbs, _submitted("TB Exception", key),
    )
    return {"config_gaps": gaps, "order": order, "completeness": completeness}


def problem_messages(problems):
    """Every message in a ``sign_off_problems`` result, in gate order."""
    messages = [g["message"] for g in problems["config_gaps"]]
    for part in ("order", "completeness"):
        if problems[part]:
            messages.append(problems[part]["message"])
    return messages


def assert_can_sign(fiscal_year, fiscal_period):
    """Throw "Sign-off blocked" listing every problem; return None when clear."""
    messages = problem_messages(sign_off_problems(fiscal_year, fiscal_period))
    if messages:
        frappe.throw("<br>".join(messages), title=BLOCKED_TITLE)


def intercompany(fiscal_year, fiscal_period):
    """The intercompany line for the sign-off signature (#305-W3-8). Never
    raises for a warehouse failure: that reads as its own ``"error"`` /
    ``"not_built"`` state (``ic_api.signoff_summary``)."""
    return ic_api.signoff_summary(fiscal_year, fiscal_period)


def assert_period_closable(fiscal_year, fiscal_period, period_type):
    """Throw unless the period may leave Open; return the signed run's name,
    or None when the period is exempt (non-Regular, or history before the
    first close period)."""
    if period_type != REGULAR:
        return None
    key = _key(fiscal_year, fiscal_period)
    first = _first_close()
    if first is None:
        frappe.throw(
            "Declare the first close period in Close Settings before closing "
            "FY%d P%02d." % key, title=CLOSE_BLOCKED_TITLE)
    if key < first:
        return None
    # Imported here: assertion_run imports this module's callers (A22).
    from konsol.consolidation.doctype.assertion_run.assertion_run import (
        assert_close_signed_off)
    return assert_close_signed_off(*key)


def _mark_latest_signed(affected, affected_by, entity=None):
    """Mark the latest signed terminal run of each period in ``affected``
    "Re-sign Needed" with ``affected_by``; return the marked run names. Each
    mark also records a ``signoff_voided`` Close Event in the caller's own
    transaction (E10-P6a, konsol#305 T04b, my judgement): a reopen or a data
    change voids a signature, and the trail should say why. ``entity``
    (S1, E2-6) scopes that event: a reopen names none (every later Regular
    period is affected, not one entity's data), while a data change caused
    by one entity's TB names it, so trail scoping can hide the void from a
    reader without access to that entity."""
    # Imported here: assertion_run imports this module's callers (A22).
    from konsol.consolidation.doctype.assertion_run.assertion_run import (
        RE_SIGN_NEEDED, SIGNED_STATES, SIGNOFF_WRITER, TERMINAL_STATUSES, writing)
    from konsol.close import close_event

    if not affected:
        return []
    latest = {}
    for r in frappe.get_all(
        "Assertion Run",
        filters={"status": ["in", list(TERMINAL_STATUSES)],
                 "signoff_status": ["in", list(SIGNED_STATES)]},
        fields=["name", "fiscal_year", "fiscal_period"],
        order_by="completed_at desc, creation desc", limit_page_length=0,
    ):
        key = _key(r["fiscal_year"], r["fiscal_period"])
        if key in affected:
            latest.setdefault(key, r["name"])

    marked = []
    for key in sorted(latest):
        name = latest[key]
        run = frappe.get_doc("Assertion Run", name)
        run.signoff_status = RE_SIGN_NEEDED
        run.affected_by = affected_by
        with writing(SIGNOFF_WRITER, name):
            # The reopener or uploader need not own the run; the mark is a
            # consequence of their action, not an edit of the run.
            run.save(ignore_permissions=True)
        # The event joins the same transaction as the mark above (neither
        # caller commits, :262-263 / :307-308 equivalents). A writer failure
        # propagates uncaught, same as T04's signed_off event (E10-P11).
        close_event.record("signoff_voided", run.fiscal_year, run.fiscal_period,
                            "Assertion Run", name, reason=affected_by, entity=entity)
        marked.append(name)
    return marked


def _regular_periods_from(target, first):
    """Every declared Regular period at or after ``target`` (a ``_key()``),
    and at or after ``first`` (the first close key, or None) when it is
    declared. Shared by a reopen (every later period, whatever its signoff
    state — ``_mark_latest_signed`` only marks the signed ones) and a data
    change (#305-W4-4 AMENDED 4 Oct: a cumulative balance sheet means a
    change also affects every later signed period, not only the changed
    one)."""
    return {
        _key(r["fiscal_year"], r["fiscal_period"])
        for r in fiscal_calendar.fiscal_period_rows()
        if r.get("period_type") == REGULAR
        and _key(r["fiscal_year"], r["fiscal_period"]) >= target
        and (first is None or _key(r["fiscal_year"], r["fiscal_period"]) >= first)
    }


def mark_resign_needed_on_reopen(fiscal_year, fiscal_period, period_code, reason, user):
    """Mark the latest signed run of the reopened Regular period
    (``fiscal_year``, ``fiscal_period``) and of every Regular period after it
    "Re-sign Needed"; return the marked run names. No commit: the reopen's
    request commits or rolls back."""
    target = _key(fiscal_year, fiscal_period)
    first = _first_close()
    affected = _regular_periods_from(target, first)
    affected_by = "FY%d %s reopened on %s by %s: %s" % (
        target[0], period_code, frappe.utils.nowdate(), user, reason)
    return _mark_latest_signed(affected, affected_by)


#: The period row's record of the last change to the data its checks read (A63).
DATA_CHANGE_FIELDS = ("data_changed_at", "data_changed_by", "data_change")


def _period_row(key, fields):
    row = frappe.db.get_value(
        "EPM Fiscal Year Period",
        {"parent": str(key[0]), "parenttype": "EPM Fiscal Year", "parentfield": "periods",
         "fiscal_period": key[1]},
        list(fields), as_dict=True,
    )
    if not row:
        frappe.throw(
            "FY%d P%02d is not declared: create it in EPM Fiscal Year." % key, PeriodNotDeclared)
    return row


def data_change(fiscal_year, fiscal_period):
    """``{data_changed_at, data_changed_by, data_change}`` of the period row;
    a blank field reads as None ("no change recorded")."""
    row = _period_row(_key(fiscal_year, fiscal_period), DATA_CHANGE_FIELDS)
    return {f: row.get(f) or None for f in DATA_CHANGE_FIELDS}


def _stamp_carried_change(source_key, affected, text, user, at):
    """Carry the data change onto every period in ``affected`` OTHER than
    ``source_key`` (#305 R41k, review-w4-server.md S2 effect 3): a cumulative
    balance sheet means ``data_change_problem`` (A66) must see the change on
    a LATER period's own row too, whether or not that period has a signed
    run for ``_mark_latest_signed`` (above) to void -- an unsigned later
    period's own check run can start before the change and still be
    refused only if its own row carries it.

    Written as ``"Balance carried from FY<y> P<p>: <text>"``, so a reader of
    that later period's own ``data_change`` never mistakes it for its own
    data changing. Skipped for a period whose OWN ``data_changed_at`` is
    already newer than ``at`` -- that period's own, real change (not a
    carried balance) is never overwritten by an earlier period's carry."""
    carried = "Balance carried from FY%d P%02d: %s" % (source_key[0], source_key[1], text)
    for key in sorted(affected):
        if key == source_key:
            continue
        row = _period_row(key, ("name",) + DATA_CHANGE_FIELDS)
        existing_at = signoff_model._as_datetime(row.get("data_changed_at"))
        if existing_at is not None and existing_at >= at:
            continue
        frappe.db.set_value(
            "EPM Fiscal Year Period", row["name"],
            {"data_changed_at": at, "data_changed_by": user, "data_change": carried},
            update_modified=False,
        )


def record_data_change(fiscal_year, fiscal_period, text, user, entity=None):
    """Record that the period's data changed (``text``, by ``user``, now) on
    its EPM Fiscal Year Period row, carry that change onto every LATER
    Regular period's own row too, and mark the changed period's AND every
    later Regular period's latest signed run "Re-sign Needed" (#305-W4-4
    AMENDED 4 Oct, Deepak "all ★", #305 issuecomment-5978983396: a balance
    sheet is cumulative, so a change to one period's data moves every later
    period's balances too — not only the changed period's). Returns the
    marked run names, one ``signoff_voided`` Close Event each
    (``_mark_latest_signed``).

    A direct row update (``db.set_value`` on the child row), so no EPM Fiscal
    Year validate runs. No commit: the caller's request commits or rolls back.
    Only the changed period's own row gets the real ``text`` / ``data_change``
    fields — that is the period whose data actually changed. Every LATER
    declared Regular period's row (R41k, review-w4-server.md S2 effect 3 —
    not only the ones ``_mark_latest_signed`` reaches, i.e. not only the
    already-signed ones) instead gets a carried marker naming the source
    period and the same text (``_stamp_carried_change``), UNLESS that later
    row already carries its own newer ``data_changed_at`` — its own, real
    change is never overwritten by an earlier period's carried balance. A
    history period (before the first close) or a non-Regular period is
    recorded but nothing is marked or carried, for the changed period or any
    later one; with no first close declared, every later Regular period is
    marked and carried (the mark errs toward re-signing, as on a reopen —
    ``_regular_periods_from``). Shared by every caller: an approval doctype's
    submit/cancel (S42) and the existing TB Submission / TB Exception callers
    inherit the later-period marking and carrying with no change on their
    side.

    ``entity`` (S1, E2-6): the entity whose data changed, when the caller can
    name one (a TB submit or cancel, a TB Exception, an amount basis set on
    a TB, or a NUMBER_DRIVING approval, S42). It is passed through to every
    ``signoff_voided`` Close Event this call writes (the changed period's and
    every later one's), so a reader scoped to other entities does not see
    why a signature stopped counting. Blank (the default) when no single
    entity caused the change.
    """
    key = _key(fiscal_year, fiscal_period)
    row = _period_row(key, ("name", "period_type"))
    at = frappe.utils.now_datetime()
    frappe.db.set_value(
        "EPM Fiscal Year Period", row["name"],
        {"data_changed_at": at, "data_changed_by": user, "data_change": text},
        update_modified=False,
    )
    if row.get("period_type") != REGULAR:
        return []
    first = _first_close()
    if first is not None and key < first:
        return []
    affected_by = "%s at %s by %s" % (text, at.strftime("%Y-%m-%d %H:%M:%S"), user)
    affected = _regular_periods_from(key, first)
    _stamp_carried_change(key, affected, text, user, at)
    return _mark_latest_signed(affected, affected_by, entity=entity)


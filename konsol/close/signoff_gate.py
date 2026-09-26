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
- ``record_data_change(fy, fp, text, user)`` (A63, #305-R2b-3): a trial
  balance or TB exception submitted or cancelled, or an amount basis set,
  changes the data a period's checks read. The period row's
  ``data_changed_at`` / ``data_changed_by`` / ``data_change`` are set (a
  direct row update: no EPM Fiscal Year validate runs), and the period's
  latest signed run is marked "Re-sign Needed" through the same writer, with
  ``affected_by`` = "<text> at <time> by <user>". History periods (before the
  first close) and non-Regular periods are recorded but never marked.
  ``sign_off_close`` refuses a run that completed before ``data_changed_at``.
- ``data_change(fy, fp)``: the period row's three fields, blanks as None.

The first close period is read from Close Settings; its Int fields read back
as 0 when unset, which ``signoff_model.first_close_key`` maps to undeclared.
No default is guessed. Nothing here is whitelisted.
"""
import datetime

import frappe

from konsol import fiscal_calendar
from konsol.close import period_model, signoff_model
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


def _frequencies(start):
    """``{entity: reporting_frequency}`` for the entities in scope at ``start``."""
    entities = frappe.get_all(
        "Entity", filters={"is_group": 0, "status": "Active"},
        fields=["name", "reporting_frequency"], limit_page_length=0,
    )
    covered = set()
    for o in frappe.get_all(
        "Ownership Period",
        filters={"docstatus": 1, "effective_date": ["<=", start], "data_area_id": ["is", "set"]},
        fields=["data_area_id", "end_date"], limit_page_length=0,
    ):
        end = _date(o["end_date"])
        if end is None or end >= start:
            covered.add(o["data_area_id"])
    return {e["name"]: e["reporting_frequency"] or "" for e in entities if e["name"] in covered}


def in_scope_entities(fiscal_year, fiscal_period):
    """Names of the entities in scope for the period, sorted."""
    row = _row(fiscal_calendar.fiscal_period_rows(), _key(fiscal_year, fiscal_period))
    return sorted(_frequencies(_date(row["start_date"])))


def _first_close():
    return signoff_model.first_close_key((
        frappe.db.get_single_value("Close Settings", "first_close_fiscal_year"),
        frappe.db.get_single_value("Close Settings", "first_close_fiscal_period"),
    ))


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
    frequencies = _frequencies(_date(row["start_date"]))
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
    completeness = signoff_model.completeness_problem(
        expected["expected"],
        _submitted("Trial Balance Submission", key),
        _submitted("TB Exception", key),
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


def _mark_latest_signed(affected, affected_by):
    """Mark the latest signed terminal run of each period in ``affected``
    "Re-sign Needed" with ``affected_by``; return the marked run names."""
    # Imported here: assertion_run imports this module's callers (A22).
    from konsol.consolidation.doctype.assertion_run.assertion_run import (
        RE_SIGN_NEEDED, SIGNED_STATES, SIGNOFF_WRITER, TERMINAL_STATUSES, writing)

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
        marked.append(name)
    return marked


def mark_resign_needed_on_reopen(fiscal_year, fiscal_period, period_code, reason, user):
    """Mark the latest signed run of the reopened Regular period
    (``fiscal_year``, ``fiscal_period``) and of every Regular period after it
    "Re-sign Needed"; return the marked run names. No commit: the reopen's
    request commits or rolls back."""
    target = _key(fiscal_year, fiscal_period)
    first = _first_close()
    affected = {
        _key(r["fiscal_year"], r["fiscal_period"])
        for r in fiscal_calendar.fiscal_period_rows()
        if r.get("period_type") == REGULAR
        and _key(r["fiscal_year"], r["fiscal_period"]) >= target
        and (first is None or _key(r["fiscal_year"], r["fiscal_period"]) >= first)
    }
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


def record_data_change(fiscal_year, fiscal_period, text, user):
    """Record that the period's data changed (``text``, by ``user``, now) on
    its EPM Fiscal Year Period row, and mark the period's latest signed run
    "Re-sign Needed". Returns the marked run names.

    A direct row update (``db.set_value`` on the child row), so no EPM Fiscal
    Year validate runs. No commit: the caller's request commits or rolls back.
    A history period (before the first close) or a non-Regular period is
    recorded but nothing is marked; with no first close declared, a Regular
    period is marked (the mark errs toward re-signing, as on a reopen).
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
    return _mark_latest_signed({key}, affected_by)


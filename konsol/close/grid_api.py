"""Period grid endpoints for the close app (konsol#305 E203, E204; stories
2.1, 2.2, 2.3; W2-2, W2-4).

``get_period_grid(fiscal_year, fiscal_period)`` returns one row per in-scope
or unowned entity (the #289 set: a submitted TB with no covering ownership)
with its Ownership, Trial balance and Closing-rate cells, as
``period_grid_model.period_grid`` assembles them. No IC column and no Checks
column (W2-4).

It reads the period in a fixed number of queries, whatever the entity
count: the calendar rows, Entity, Ownership Period, Trial Balance
Submission, TB Exception, the root Consolidation Group's reporting
currency, the period's Closing Group Exchange Rates, one plain (no lock)
``group_rates.rate_gate`` and ``allowed_entity_codes``; then, when any row
is visible, one ``close_event.reminders`` read (topic tb) and, when a
visible row was reminded, one User read for the senders' full names
(konsol#305 Y57); one ``deadlines.period_deadlines`` read (its three
queries: the rules, the holidays and the calendar) and one
``assertion_run.latest_close_run`` read (konsol#305 D57); only when the IC
due date is past, one ``ic_api.signoff_summary`` read (its own reads: the
Intercompany Account count, Close Settings, the two ClickHouse IC tables and
the sent-back Close Events), and only when the journals due date is past, one
Consolidation Journal count (konsol#305 D57b). Nothing is read per entity,
and nothing is written.

Each row's Trial balance cell carries ``reminders``: ``{count, last_at,
last_by, last_by_name}`` or None (C-R6). Only visible rows are filled, so a
hidden entity's reminders never leave the server.

Deadlines (konsol#305 D57, decision #305-2.4-1; show-only, they block
nothing): ``deadlines`` is the period's four steps (tb, ic, journals,
signoff) as ``{due, past, text}``; an undeclared step reads "No due date
declared", never a guessed date. Each TB cell carries ``overdue``: the TB
due date is past and the cell is ``Missing`` (C-D4: past AND the step still
open); every other cell is not overdue. ``signoff_overdue`` is the sign-off
date past and the period not signed: the latest close run's
``signoff_status`` is not in ``signoff_model.SIGNED_STATES`` (no run and
"Re-sign Needed" count as not signed).

IC and journals overdue (konsol#305 D57b, decision #305-Q5-1, Deepak Pai,
7 Oct): ``ic_overdue`` is the IC date past and at least one pair over
tolerance (``deadline_model.ic_open``); ``journals_overdue`` is the journals
date past and at least one Consolidation Journal of the period a draft or
pending approval, docstatus 0 (``deadline_model.journals_open``). An
undeclared or not-yet-past date is False and reads nothing. An unreadable IC
warehouse or journal count throws (the grid's error state), never False.
Intercompany not configured or declared not applicable has no pair to be
open: False.

The Entity Accountant is not a grid role (E2-7): the grid is an all-entity
read of group configuration. Rows are still cut to the caller's permitted
entities, with a ``hidden`` count (E2-6).

``get_readiness(fiscal_year, fiscal_period)`` returns the readiness
checklist (``readiness_model.readiness``) for a Regular period, built from
the same sign-off gate the sign-off screen uses
(``signoff_gate.sign_off_problems``), the same plain ``group_rates.rate_gate``
the grid uses, the latest terminal close run
(``assertion_run.latest_close_run``) and ``allowed_entity_codes``. Each
reader is called once. The strip and the gate read the same
``sign_off_problems`` call, so they cannot disagree about a gap.
"""
import datetime

import frappe

from konsol import fiscal_calendar, group_rates
from konsol.close import period_grid_model, readiness_model, signoff_gate, signoff_model
from konsol.consolidation.doctype.assertion_run.assertion_run import latest_close_run
from konsol.entity_permissions import allowed_entity_codes
from konsol.period_status import PeriodNotDeclared

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
#: The Remind rules (konsol#305 Y52), pure: the reminder summary.
remind_model = _load_by_path("remind_model.py", "konsol_close_remind_model")
#: Zoned ISO for the reminders' ``last_at`` (A16b), pure.
zoned_iso = _load_by_path("timefmt.py", "konsol_close_timefmt").zoned_iso
#: The IC and journals "open" rules (konsol#305 D57b, #305-Q5-1), pure.
deadline_model = _load_by_path("deadline_model.py", "konsol_close_deadline_model")

REGULAR = "Regular"
CLOSING = "Closing"


def _regular_row(key, rows):
    """The calendar row of ``key``; refuses an undeclared or non-Regular period.

    Wording copied from tb_read_api.py ``_regular_row``.
    """
    for row in rows:
        if (int(row["fiscal_year"]), int(row["fiscal_period"])) == key:
            break
    else:
        frappe.throw(
            "%s is not declared: create it in EPM Fiscal Year." % period_name(*key), PeriodNotDeclared
        )
    if row.get("period_type") != REGULAR:
        frappe.throw(
            "%s is a %s period; the period grid covers Regular periods only: "
            "pick a Regular period." % (period_name(*key), row.get("period_type") or "blank-type")
        )
    return row


def _iso(value):
    if isinstance(value, (datetime.date, datetime.datetime)):
        return value.isoformat()
    return None if value in (None, "") else str(value)


def _by_entity(doctype, key, fields):
    """Submitted records of the period, first per entity (tb_read_api ``_records`` shape)."""
    out = {}
    for r in frappe.get_all(
        doctype,
        filters={"fiscal_year": key[0], "fiscal_period": key[1], "docstatus": 1},
        fields=["data_area_id"] + fields, limit_page_length=0,
    ):
        out.setdefault(r["data_area_id"], r)
    return out


def _rates(key):
    """The ``rates`` input of ``period_grid_model.rate_cell``."""
    group_currencies = set(frappe.get_all(
        "Consolidation Group",
        filters={"data_area_id": ["is", "not set"], "reporting_currency": ["is", "set"]},
        pluck="reporting_currency", limit_page_length=0,
    ))
    approved, drafts = set(), set()
    for r in frappe.get_all(
        "Group Exchange Rate",
        filters={"fiscal_year": key[0], "fiscal_period": key[1], "rate_type": CLOSING,
                 "docstatus": ["<", 2]},
        fields=["from_currency", "to_currency", "docstatus"], limit_page_length=0,
    ):
        (approved if int(r["docstatus"]) == 1 else drafts).add(
            (r["from_currency"], r["to_currency"]))
    missing, error, _blockers = group_rates.rate_gate(key[0], key[1])
    return {"group_currencies": group_currencies, "missing": missing, "error": error,
            "approved": approved, "drafts": drafts}


def _reminders(key, codes):
    """``{entity: {count, last_at, last_by, last_by_name}}`` for the visible
    ``codes`` (konsol#305 Y57, C-R6, as tb_read_api ``_reminders``): one
    ``close_event.reminders`` read for the period and topic tb, summarised by
    ``remind_model.summary``. Only the visible entities' entries are kept, so
    a hidden entity's reminders (and who sent them) never leave the server.
    An unreadable event raises (``summary``): a count is never guessed as 0.
    A sender with no full name is refused, never shown as a user id."""
    from konsol.close import close_event  # lazy: the ic_api.send_back precedent

    summary = remind_model.summary(close_event.reminders([key], "tb"))
    entries = {}
    for (fy, fp, entity, topic), entry in summary.items():
        if (fy, fp) == key and topic == "tb" and entity in codes:
            entries[entity] = entry
    if not entries:
        return {}
    actors = sorted({e["last_by"] for e in entries.values()})
    names = {u["name"]: u.get("full_name") for u in frappe.get_all(
        "User", filters={"name": ["in", actors]}, fields=["name", "full_name"],
        limit_page_length=0)}
    for actor in actors:
        if not names.get(actor):
            frappe.throw("User %s, who sent the last reminder, has no full name: set the "
                         "user's First Name in User." % actor)
    tz = frappe.utils.get_system_timezone()
    return {entity: {"count": int(e["count"]), "last_at": zoned_iso(e["last_at"], tz),
                     "last_by": e["last_by"], "last_by_name": names[e["last_by"]]}
            for entity, e in entries.items()}


def _deadlines(key):
    """The period's four steps as ``{step: {due, past, text}}`` (konsol#305
    D57): one ``deadlines.period_deadlines`` read with today's date. The asked
    period is Regular (``_regular_row``), so the reader must return it: its
    absence is an error, never "No due date declared"."""
    from konsol.close import deadlines  # lazy (C-X1)

    found = deadlines.period_deadlines([key], frappe.utils.getdate())
    if key not in found:
        frappe.throw("No deadline was read for %s, a Regular period: check its row in "
                     "EPM Fiscal Year." % period_name(*key))
    return deadlines.as_payload(found[key])


def _ic_overdue(key, due):
    """``ic_overdue`` (konsol#305 D57b, #305-Q5-1): the IC date is past and a
    pair is still over tolerance (``deadline_model.ic_open``). Read only when
    the date is past: one ``ic_api.signoff_summary`` read (the sign-off
    summary's own IC counts). An unreadable warehouse throws (the grid's
    error state), never False."""
    if not due["ic"]["past"]:
        return False
    from konsol.close import ic_api  # lazy (C-X1): ic_api imports ch_read

    summary = ic_api.signoff_summary(*key)
    try:
        return deadline_model.ic_open(summary["state"], summary["counts"])
    except ValueError as e:
        frappe.throw("%s for %s. %s" % (e, period_name(*key), summary.get("message") or ""))


def _journals_overdue(key, due):
    """``journals_overdue`` (konsol#305 D57b, #305-Q5-1): the journals date is
    past and a Consolidation Journal of the period is a draft or pending
    approval (docstatus 0, ``deadline_model.journals_open``). Read only when
    the date is past: one count. A failed count raises, never False."""
    if not due["journals"]["past"]:
        return False
    count = frappe.db.count("Consolidation Journal",
                            {"fiscal_year": key[0], "fiscal_period": key[1], "docstatus": 0})
    try:
        return deadline_model.journals_open(count)
    except ValueError as e:
        frappe.throw("%s for %s." % (e, period_name(*key)))


def _signed(run):
    """The latest close run is signed: its ``signoff_status`` is one of
    ``signoff_model.SIGNED_STATES``. No run, "Not Signed Off" and "Re-sign
    Needed" are not signed (coordinator call on D57)."""
    return bool(run) and run.get("signoff_status") in signoff_model.SIGNED_STATES


@frappe.whitelist(methods=["GET"])
def get_period_grid(fiscal_year, fiscal_period):
    """``{period, rows, counts, rates_error, deadlines, signoff_overdue,
    ic_overdue, journals_overdue}`` for a Regular period.

    ``period`` = ``{fiscal_year, fiscal_period, code, status, start_date}``
    (ISO date). ``rows``, ``counts`` and ``rates_error`` are
    ``period_grid_model.period_grid``'s; each row's ``tb`` cell also
    carries ``reminders`` (konsol#305 Y57) and ``overdue`` (D57).
    ``deadlines`` and the three ``*_overdue`` flags: see the module docstring.
    Read-only. Refuses an undeclared
    period (PeriodNotDeclared) and a non-Regular one.
    """
    # A literal: the endpoint contract test reads it. No Entity Accountant (E2-7).
    frappe.only_for(("EPM Admin", "EPM Analyst", "EPM User", "System Manager"))
    key = (int(fiscal_year), int(fiscal_period))
    rows = fiscal_calendar.fiscal_period_rows()
    row = _regular_row(key, rows)
    start = row["start_date"]

    entities = frappe.get_all(
        "Entity", filters={"is_group": 0},
        fields=["name", "entity_name", "status", "functional_currency", "reporting_frequency"],
        limit_page_length=0,
    )
    ownership_rows = frappe.get_all(
        "Ownership Period",
        filters={"docstatus": 1, "effective_date": ["<=", start], "data_area_id": ["is", "set"]},
        fields=["data_area_id", "effective_date", "end_date", "consolidation_group",
                "ownership_pct", "consolidation_method"],
        limit_page_length=0,
    )
    tbs = _by_entity("Trial Balance Submission", key, ["name", "owner", "uploaded_on_behalf"])
    exceptions = _by_entity("TB Exception", key, ["name"])
    rates = _rates(key)
    allowed = allowed_entity_codes()
    due = _deadlines(key)
    run = latest_close_run(*key)

    grid = period_grid_model.period_grid(
        key, rows, entities, ownership_rows, tbs, exceptions, rates, allowed)
    if grid["rows"]:
        reminded = _reminders(key, {r["entity"] for r in grid["rows"]})
        for r in grid["rows"]:
            r["tb"] = dict(r["tb"], reminders=reminded.get(r["entity"]))
    for r in grid["rows"]:
        r["tb"]["overdue"] = bool(
            r["tb"]["label"] == period_grid_model.MISSING and due["tb"]["past"])
    period = {"fiscal_year": key[0], "fiscal_period": key[1], "code": row.get("period_code"),
              "status": row.get("status"), "start_date": _iso(start)}
    return dict({"period": period}, **grid, deadlines=due,
                signoff_overdue=bool(due["signoff"]["past"] and not _signed(run)),
                ic_overdue=_ic_overdue(key, due), journals_overdue=_journals_overdue(key, due))


@frappe.whitelist(methods=["GET"])
def get_readiness(fiscal_year, fiscal_period):
    """``readiness_model.readiness(...)`` for a Regular period: the
    readiness checklist shown in the close app's readiness strip. Reads the
    same gate the sign-off screen uses (``signoff_gate.sign_off_problems``),
    so the strip and the gate can never disagree about a gap. Read-only.
    Refuses an undeclared period (PeriodNotDeclared) and a non-Regular one,
    before any reader runs.
    """
    # A literal: the endpoint contract test reads it. No Entity Accountant (E2-7).
    frappe.only_for(("EPM Admin", "EPM Analyst", "EPM User", "System Manager"))
    key = (int(fiscal_year), int(fiscal_period))
    rows = fiscal_calendar.fiscal_period_rows()
    row = _regular_row(key, rows)

    problems = signoff_gate.sign_off_problems(*key)
    rates = group_rates.rate_gate(*key)
    run = latest_close_run(*key)
    allowed = allowed_entity_codes()

    period_row = {"code": row.get("period_code"), "status": row.get("status"),
                  "period_type": row.get("period_type")}
    return readiness_model.readiness(period_row, problems, rates, run, allowed)

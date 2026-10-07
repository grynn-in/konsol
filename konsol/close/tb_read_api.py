"""Trial balance read endpoints for the close app (konsol#305 A25; stories 3.1, 3.7).

``my_tbs(fiscal_year, fiscal_period)`` lists the caller's entities for a
Regular period, each with one status:

- ``Received``: a submitted Trial Balance Submission for the period.
- ``Exception declared``: a submitted TB Exception for the period.
- ``Not expected this period``: a Quarterly entity outside a quarter-end.
- ``Frequency not declared``: a blank ``Entity.reporting_frequency``. No
  frequency is assumed (no policy defaults).
- ``Quarter not declared``: a Quarterly entity in a year whose Regular periods
  do not all declare a Quarter, so the quarter-end is unknowable.
- ``Missing``: expected, and neither received nor excepted.

Entities are the in-scope entities (``signoff_gate.in_scope_entities``, A17)
the caller may see (``entity_permissions.allowed_entity_codes``: None means
all, an empty set means none). Entity scope is a security boundary: every query
is limited to the visible entities, and expectations are computed over them
only, so no other entity's code appears in the result.

- ``Not consolidated: no ownership for this period``: besides the in-scope
  entities, every entity the caller may see that submitted a trial balance
  for the period with no covering ownership (#289). The set is exactly the
  sign-off gate's ``tb_without_ownership`` gap
  (``signoff_gate.sign_off_problems``), never re-derived.

The on-behalf label (R4, konsol#297) follows ``uploaded_on_behalf`` (A18):
"Yes" -> "by <owner> for <entity>", "No" -> "by <owner>", blank -> unknown
(uploaded before it was recorded; Problems 16), never read as "No".

The TB due date (konsol#305 D56, decision #305-2.4-1) is the ``tb`` step of
``deadlines.period_deadlines`` for the period: ``deadline`` is ``{due, past,
text}``, and an undeclared rule reads "No due date declared", never a guessed
date. A ``Missing`` row is ``overdue`` when the due date is past; every other
row is not (C-D4: overdue is past AND the step still open). Overdue is shown
only; it blocks nothing.

``tb_compare(entity, fiscal_year, fiscal_period)`` (A28, story 3.4) compares
the entity's submitted trial balance with the one of the previous declared
Regular period (``tb_view_model.previous_period``, A12: across a year end,
never an Opening, Closing or Adjustment period). The files are read as the
submission reads them (``File.get_content``, BOM stripped, ``parse_tb_csv``)
and joined by ``tb_view_model.compare``. No previous trial balance is a note,
never a comparison against zero. The entity is checked first
(``assert_entity_access``): another entity's TB is never read.
"""
import datetime

import frappe

from konsol import fiscal_calendar
from konsol.close import signoff_gate, signoff_model, tb_view_model
from konsol.close.timefmt import zoned_iso
from konsol.consolidation.doctype.trial_balance_submission.trial_balance_submission import (
    parse_tb_csv,
)
from konsol.entity_permissions import allowed_entity_codes, assert_entity_access
from konsol.tb_basis_model import AMOUNT_BASES
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
#: The Remind rules (konsol#305 Y52), pure: who may remind, and the summary.
remind_model = _load_by_path("remind_model.py", "konsol_close_remind_model")

REGULAR = "Regular"
OPEN = "Open"

RECEIVED = "Received"
EXCEPTION_DECLARED = "Exception declared"
NOT_EXPECTED = "Not expected this period"
MISSING = "Missing"
FREQUENCY_NOT_DECLARED = "Frequency not declared"
QUARTER_NOT_DECLARED = "Quarter not declared"
NOT_CONSOLIDATED = "Not consolidated: no ownership for this period"

#: every status my_tbs can emit (E209b's screen drift guard reads this).
TB_STATUSES = (RECEIVED, EXCEPTION_DECLARED, NOT_EXPECTED, MISSING,
               FREQUENCY_NOT_DECLARED, QUARTER_NOT_DECLARED, NOT_CONSOLIDATED)

# Missing first (story 3.1: what needs doing), then the configuration gaps,
# then the settled statuses; entity code within each. Not consolidated (#289)
# ranks with Missing: someone must act.
_RANK = {MISSING: 0, NOT_CONSOLIDATED: 0, FREQUENCY_NOT_DECLARED: 1, QUARTER_NOT_DECLARED: 1}

_ON_BEHALF = {"Yes": True, "No": False}


def _iso(value):
    """A datetime with the site's UTC offset (A55: Frappe stores it naive in
    the system time zone); a plain date stays a date."""
    if isinstance(value, datetime.datetime):
        return zoned_iso(value, frappe.utils.get_system_timezone())
    if isinstance(value, datetime.date):
        return value.isoformat()
    return None if value in (None, "") else str(value)


def _regular_row(key, rows=None):
    for row in (fiscal_calendar.fiscal_period_rows() if rows is None else rows):
        if (int(row["fiscal_year"]), int(row["fiscal_period"])) == key:
            break
    else:
        frappe.throw(
            "%s is not declared: create it in EPM Fiscal Year." % period_name(*key), PeriodNotDeclared
        )
    if row.get("period_type") != REGULAR:
        frappe.throw(
            "%s is a %s period; the trial balance list covers Regular periods only: "
            "pick a Regular period." % (period_name(*key), row.get("period_type") or "blank-type")
        )
    return row


def _visible(key, allowed):
    """In-scope entity codes the caller may see, sorted."""
    scope = signoff_gate.in_scope_entities(*key)
    return sorted(e for e in scope if allowed is None or e in allowed)


def _unowned(key, allowed):
    """Entities with a submitted TB and no covering ownership for the period
    (#289), cut to ``allowed``, sorted. This is exactly the sign-off gate's
    ``tb_without_ownership`` gap; never re-derived."""
    problems = signoff_gate.sign_off_problems(*key)
    gap = sorted(e for g in problems["config_gaps"] if g["code"] == signoff_model.UNOWNED_TB
                 for e in g.get("entities") or ())
    return [e for e in gap if allowed is None or e in allowed]


def _records(doctype, key, entities, fields):
    """Submitted records of the period for the given entities, by entity."""
    out = {}
    for r in frappe.get_all(
        doctype,
        filters={"fiscal_year": key[0], "fiscal_period": key[1], "docstatus": 1,
                 "data_area_id": ["in", entities]},
        fields=["data_area_id"] + fields, limit_page_length=0,
    ):
        out.setdefault(r["data_area_id"], r)
    return out


def _tb(record, entity):
    if record is None:
        return None
    owner = record["owner"]
    on_behalf = _ON_BEHALF.get(record.get("uploaded_on_behalf") or "")
    if on_behalf is True:
        label = "by %s for %s" % (owner, entity)
    elif on_behalf is False:
        label = "by %s" % owner
    else:
        label = "by %s (on behalf: not recorded)" % owner
    return {"name": record["name"], "owner": owner, "on_behalf": on_behalf,
            "on_behalf_label": label, "creation": _iso(record.get("creation"))}


def _exception(record):
    if record is None:
        return None
    return {"name": record["name"], "reason": record.get("reason"),
            "declared_by": record.get("declared_by"),
            "declared_on": _iso(record.get("creation"))}


def _reminders(key, codes):
    """``{entity: {count, last_at, last_by, last_by_name}}`` for the visible
    ``codes`` (konsol#305 Y56, C-R6): one ``close_event.reminders`` read for
    the period and topic tb, summarised by ``remind_model.summary``. Only the
    visible entities' entries are kept, so a hidden entity's reminders (and
    who sent them) never leave the server. An unreadable event raises
    (``summary``): a count is never guessed as 0. A sender with no full name
    is refused, never shown as a user id."""
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
    return {entity: {"count": int(e["count"]), "last_at": _iso(e["last_at"]),
                     "last_by": e["last_by"], "last_by_name": names[e["last_by"]]}
            for entity, e in entries.items()}


def _tb_deadline(key):
    """The period's TB due date as ``{due, past, text}`` (konsol#305 D56): one
    ``deadlines.period_deadlines`` read with today's date. The asked period is
    Regular (``_regular_row``), so the reader must return it: its absence is
    an error, never "No due date declared"."""
    from konsol.close import deadlines  # lazy (C-X1)

    found = deadlines.period_deadlines([key], frappe.utils.getdate())
    if key not in found:
        frappe.throw("No deadline was read for %s, a Regular period: check its row in "
                     "EPM Fiscal Year." % period_name(*key))
    return deadlines.as_payload(found[key])["tb"]


@frappe.whitelist(methods=["GET"])
def my_tbs(fiscal_year, fiscal_period):
    """``{period_open, can_upload, can_remind, deadline, entities: [{entity,
    name, status, tb, exception, reminders, overdue}]}``.

    ``deadline`` is the TB step's ``{due, past, text}`` (D56); ``overdue`` is
    ``deadline.past`` on a ``Missing`` row and False on every other row.

    ``reminders`` is ``{count, last_at, last_by, last_by_name}`` or None
    (topic tb, konsol#305 Y56). ``can_remind`` is True only for
    ``remind_model.REMIND_ROLES`` in an Open period.

    Read-only. Refuses an undeclared period (PeriodNotDeclared) and a
    non-Regular one (only Regular periods are gated, P5).
    """
    # Every close role reads; the EPM Analyst and the Viewer only read.
    # A literal: the endpoint contract test reads it.
    frappe.only_for(("EPM Admin", "EPM Analyst", "Entity Accountant", "EPM User", "System Manager"))
    key = (int(fiscal_year), int(fiscal_period))
    row = _regular_row(key)
    period_open = row.get("status") == OPEN
    result = {
        "period_open": period_open,
        "can_upload": bool(period_open and frappe.has_permission("Trial Balance Submission", "create")),
        "can_remind": bool(period_open
                           and set(frappe.get_roles()) & set(remind_model.REMIND_ROLES)),
        "deadline": _tb_deadline(key),
        "entities": [],
    }

    allowed = allowed_entity_codes()
    if allowed is not None and not allowed:
        return result

    visible = _visible(key, allowed)
    unowned = _unowned(key, allowed)
    all_codes = sorted(set(visible) | set(unowned))
    if not all_codes:
        return result

    entities = {e["name"]: e for e in frappe.get_all(
        "Entity", filters={"name": ["in", all_codes]},
        fields=["name", "entity_name", "reporting_frequency"], limit_page_length=0,
    )}
    frequencies = {code: (entities[code].get("reporting_frequency") or "")
                   for code in visible if code in entities}
    expected = signoff_model.expected_entities(
        frequencies, key, fiscal_calendar.fiscal_period_rows())
    quarter_unknown = {e for g in expected["gaps"]
                       if g["code"] == signoff_model.QUARTER_UNDECLARED for e in g["entities"]}
    tbs = _records("Trial Balance Submission", key, all_codes,
                   ["name", "owner", "uploaded_on_behalf", "creation"])
    exceptions = _records("TB Exception", key, all_codes,
                          ["name", "reason", "declared_by", "creation"])

    out = []
    for code in frequencies:
        if code in tbs:
            status = RECEIVED
        elif code in exceptions:
            status = EXCEPTION_DECLARED
        elif code in expected["frequency_undeclared"]:
            status = FREQUENCY_NOT_DECLARED
        elif code in quarter_unknown:
            status = QUARTER_NOT_DECLARED
        elif code in expected["not_expected"]:
            status = NOT_EXPECTED
        else:
            status = MISSING
        out.append({
            "entity": code,
            "name": entities[code].get("entity_name") or code,
            "status": status,
            "tb": _tb(tbs.get(code), code),
            "exception": _exception(exceptions.get(code)),
        })
    for code in unowned:
        if code in frequencies:
            continue  # G01 interface: an unowned entity is never in scope too.
        out.append({
            "entity": code,
            "name": entities[code].get("entity_name") or code,
            "status": NOT_CONSOLIDATED,
            "tb": _tb(tbs.get(code), code),
            "exception": None,
        })
    reminded = _reminders(key, {e["entity"] for e in out})
    for e in out:
        e["reminders"] = reminded.get(e["entity"])
        e["overdue"] = bool(e["status"] == MISSING and result["deadline"]["past"])
    out.sort(key=lambda e: (_RANK.get(e["status"], 2), e["entity"]))
    result["entities"] = out
    return result


def _submitted_tb(entity, key):
    """The entity's submitted Trial Balance Submission for the period, or None."""
    found = frappe.get_all(
        "Trial Balance Submission",
        filters={"data_area_id": entity, "fiscal_year": key[0], "fiscal_period": key[1],
                 "docstatus": 1},
        fields=["name", "tb_file", "amount_basis"], order_by="creation desc",
        limit_page_length=0,
    )
    return found[0] if found else None


def _basis(tb):
    if tb.get("amount_basis") not in AMOUNT_BASES:
        frappe.throw(
            "Trial balance %s declares no amount basis (%r): set it with Set amount basis "
            "on the Trial Balance Submission list." % (tb["name"], tb.get("amount_basis") or ""))
    return tb["amount_basis"]


def _tb_rows(tb):
    """Parsed rows of a submitted TB's file, read as the submission reads it."""
    if not tb.get("tb_file"):
        frappe.throw("Trial balance %s has no file attached: attach its CSV." % tb["name"])
    content = frappe.get_doc("File", {"file_url": tb["tb_file"]}).get_content()
    content = (content.decode("utf-8-sig") if isinstance(content, bytes)
               else content.lstrip("\ufeff"))
    try:
        # Read back, not re-judged: a stored file stays readable whatever the
        # site's dimensions or intake rules are today (konsol#255). No
        # declared dimensions are passed, so the read depends only on the file;
        # the compare keys on account and partner and reads no dimension.
        return parse_tb_csv(content, (), stored=True)
    except ValueError as e:
        frappe.throw("Could not read the file of trial balance %s: %s" % (tb["name"], e))


def _code(row, key):
    """The period's name, always with its year (review-w5): the live
    period_code ("P12") is the same in every year. ``key`` is unused and
    kept for the callers."""
    return period_name(row["fiscal_year"], row["fiscal_period"])


@frappe.whitelist(methods=["GET"])
def tb_compare(entity, fiscal_year, fiscal_period):
    """This period's trial balance against the previous period's, by account.

    Returns the ``tb_view_model.compare`` result (``rows``, ``basis_note``,
    ``previous_note``, ``previous_code``) plus ``entity``, ``current`` and
    ``previous`` (``{fiscal_year, fiscal_period, code, tb, basis}``;
    ``previous`` is None when the calendar declares no previous Regular
    period, and its ``tb``/``basis`` are None when that period has no
    submitted TB). Read-only. Refuses an entity the caller may not access,
    an undeclared or non-Regular period, a period with no submitted TB, and
    a TB with no declared amount basis.
    """
    # A literal: the endpoint contract test reads it.
    frappe.only_for(("EPM Admin", "EPM Analyst", "Entity Accountant", "EPM User", "System Manager"))
    assert_entity_access(entity)
    key = (int(fiscal_year), int(fiscal_period))
    period_rows = fiscal_calendar.fiscal_period_rows()
    row = _regular_row(key, period_rows)
    code = _code(row, key)

    current_tb = _submitted_tb(entity, key)
    if current_tb is None:
        frappe.throw("No submitted trial balance for %s %s: submit one for the period first."
                     % (entity, code))
    current_basis = _basis(current_tb)

    prev_row = tb_view_model.previous_period(period_rows, key[0], key[1])
    previous = None
    previous_rows = previous_basis = previous_code = None
    if prev_row is not None:
        prev_key = (int(prev_row["fiscal_year"]), int(prev_row["fiscal_period"]))
        previous_code = _code(prev_row, key)
        previous_tb = _submitted_tb(entity, prev_key)
        if previous_tb is not None:
            previous_basis = _basis(previous_tb)
        previous = {"fiscal_year": prev_key[0], "fiscal_period": prev_key[1],
                    "code": previous_code,
                    "tb": previous_tb["name"] if previous_tb else None,
                    "basis": previous_basis}
        if previous_tb is not None:
            previous_rows = _tb_rows(previous_tb)

    result = tb_view_model.compare(_tb_rows(current_tb), previous_rows, current_basis,
                                   previous_basis, previous_code)
    result.update({
        "entity": entity,
        "current": {"fiscal_year": key[0], "fiscal_period": key[1], "code": code,
                    "tb": current_tb["name"], "basis": current_basis},
        "previous": previous,
    })
    return result

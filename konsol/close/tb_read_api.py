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

The on-behalf label (R4, konsol#297) follows ``uploaded_on_behalf`` (A18):
"Yes" -> "by <owner> for <entity>", "No" -> "by <owner>", blank -> unknown
(uploaded before it was recorded; Problems 16), never read as "No".

No due date is shown: nothing declares one (Problems 6).

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
from konsol.consolidation.doctype.trial_balance_submission.trial_balance_submission import (
    parse_tb_csv,
)
from konsol.entity_permissions import allowed_entity_codes, assert_entity_access
from konsol.tb_basis_model import AMOUNT_BASES
from konsol.period_status import PeriodNotDeclared

REGULAR = "Regular"
OPEN = "Open"

RECEIVED = "Received"
EXCEPTION_DECLARED = "Exception declared"
NOT_EXPECTED = "Not expected this period"
MISSING = "Missing"
FREQUENCY_NOT_DECLARED = "Frequency not declared"
QUARTER_NOT_DECLARED = "Quarter not declared"

# Missing first (story 3.1: what needs doing), then the configuration gaps,
# then the settled statuses; entity code within each.
_RANK = {MISSING: 0, FREQUENCY_NOT_DECLARED: 1, QUARTER_NOT_DECLARED: 1}

_ON_BEHALF = {"Yes": True, "No": False}


def _iso(value):
    if isinstance(value, (datetime.date, datetime.datetime)):
        return value.isoformat()
    return None if value in (None, "") else str(value)


def _regular_row(key, rows=None):
    for row in (fiscal_calendar.fiscal_period_rows() if rows is None else rows):
        if (int(row["fiscal_year"]), int(row["fiscal_period"])) == key:
            break
    else:
        frappe.throw(
            "FY%d P%02d is not declared: create it in EPM Fiscal Year." % key, PeriodNotDeclared
        )
    if row.get("period_type") != REGULAR:
        frappe.throw(
            "FY%d P%02d is a %s period; the trial balance list covers Regular periods only: "
            "pick a Regular period." % (key[0], key[1], row.get("period_type") or "blank-type")
        )
    return row


def _visible(key):
    """In-scope entity codes the caller may see, sorted."""
    allowed = allowed_entity_codes()
    if allowed is not None and not allowed:
        return []
    scope = signoff_gate.in_scope_entities(*key)
    return sorted(e for e in scope if allowed is None or e in allowed)


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
            "declared_by": record.get("declared_by")}


@frappe.whitelist(methods=["GET"])
def my_tbs(fiscal_year, fiscal_period):
    """``{period_open, can_upload, entities: [{entity, name, status, tb, exception}]}``.

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
        "entities": [],
    }

    visible = _visible(key)
    if not visible:
        return result

    entities = {e["name"]: e for e in frappe.get_all(
        "Entity", filters={"name": ["in", visible]},
        fields=["name", "entity_name", "reporting_frequency"], limit_page_length=0,
    )}
    frequencies = {code: (entities[code].get("reporting_frequency") or "")
                   for code in visible if code in entities}
    expected = signoff_model.expected_entities(
        frequencies, key, fiscal_calendar.fiscal_period_rows())
    quarter_unknown = {e for g in expected["gaps"]
                       if g["code"] == signoff_model.QUARTER_UNDECLARED for e in g["entities"]}
    tbs = _records("Trial Balance Submission", key, visible,
                   ["name", "owner", "uploaded_on_behalf", "creation"])
    exceptions = _records("TB Exception", key, visible, ["name", "reason", "declared_by"])

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
        return parse_tb_csv(content)
    except ValueError as e:
        frappe.throw("Could not read the file of trial balance %s: %s" % (tb["name"], e))


def _code(row, key):
    """The period's code; qualified with the year when it is not ``key``'s year."""
    code = row.get("period_code") or "P%02d" % int(row["fiscal_period"])
    return code if int(row["fiscal_year"]) == key[0] else "FY%d %s" % (int(row["fiscal_year"]), code)


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

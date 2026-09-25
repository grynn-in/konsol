"""Trial balance endpoints for the close app (konsol#305, story 3.2).

``check_tb`` judges a trial-balance CSV and persists nothing (D1). It is POST
only because a file does not fit in a query string: no File is uploaded, no
Trial Balance Upload or Submission is created, nothing is written to MariaDB or
ClickHouse. (``tb_bulk.check_file`` is not this pattern: it inserts a Trial
Balance Upload, and its client uploads a File first — Problems 2.)

``submit_tb`` submits a checked file, replacing the entity-period's submitted
TB when there is one (A24, stories 3.3/3.5/3.7).
"""

import frappe

from konsol.clickhouse import execute
from konsol.close.tb_model import check_rows
from konsol.consolidation.doctype.trial_balance_submission.trial_balance_submission import (
    BALANCE_TOLERANCE,
    CONTROL_TABLE,
    PARTNER,
    _claim_insert,
    _claim_values,
    _sql_str,
    parse_tb_csv,
)
from konsol.entity_permissions import assert_entity_access
from konsol.group_chart import chart_accounts
from konsol.period_status import OPEN, assert_open, assert_postable, period_row

#: How many problems a refused submit names; the check screen shows them all.
_FIRST_PROBLEMS = 5

def _text(content):
    """The CSV as text, without the byte-order mark Excel's "CSV UTF-8" writes
    (mirrors TrialBalanceSubmission._parse_file)."""
    if content is None:
        return ""
    if isinstance(content, bytes):
        return content.decode("utf-8-sig")
    return content.lstrip("﻿")


def _partner_entities(rows):
    """The non-group Entities a partner may name, or None when no row names a
    partner (mirrors TrialBalanceSubmission._partner_entities: get_all, since a
    partner is named, not read)."""
    if not any(r.get(PARTNER) for r in rows):
        return None
    return set(frappe.get_all("Entity", filters={"is_group": 0}, pluck="name",
                              limit_page_length=0))


def _submitted(entity, fiscal_year, fiscal_period):
    """The submitted Trial Balance Submission for the entity-period, or None."""
    return frappe.db.get_value(
        "Trial Balance Submission",
        {"data_area_id": entity, "fiscal_year": fiscal_year,
         "fiscal_period": fiscal_period, "docstatus": 1},
        "name",
    )


@frappe.whitelist(methods=["POST"])
def check_tb(entity, fiscal_year, fiscal_period, amount_basis, content):
    """Check a trial-balance CSV for one entity-period. Writes nothing.

    Returns the ``tb_model.check_rows`` result
    (``{ok, rows, file_problems, totals}``) plus ``period_problem`` (a sentence
    when the period is not Open, else None; ``ok`` judges the file only) and
    ``replaces`` (the submitted TB a submit would replace, else None).

    Refuses (throws) a caller outside the roles, an entity outside their scope
    (PermissionError), an entity that is not an existing non-group Entity, an
    undeclared period (PeriodNotDeclared) and a period type that does not take
    trial balances on this site. A file that cannot be read is a file problem.
    """
    # The Close Lead, the Entity Accountant (scoped by User Permission on Entity)
    # and System Manager. A literal: the endpoint contract test reads it.
    frappe.only_for(("EPM Admin", "Entity Accountant", "System Manager"))
    assert_entity_access(entity)
    if not frappe.db.exists("Entity", {"name": entity, "is_group": 0}):
        frappe.throw(f"Entity {entity} is not an entity a trial balance can be submitted for: "
                     "pick an existing entity that is not a group.")

    assert_postable(fiscal_year, fiscal_period)
    period = period_row(fiscal_year, fiscal_period)
    year, number = period["fiscal_year"], period["fiscal_period"]
    period_problem = None
    if period["status"] != OPEN:
        period_problem = f"FY{year} P{number} is {period['status']}: a trial balance can't be submitted"

    try:
        rows = parse_tb_csv(_text(content))
        read_problem = None
    except ValueError as e:
        rows = []
        read_problem = f"Could not read the trial balance file: {e}"

    result = check_rows(rows, chart_accounts(), entity, _partner_entities(rows),
                        amount_basis, BALANCE_TOLERANCE)
    if read_problem:
        result["file_problems"].insert(0, read_problem)
        result["ok"] = False
    result["period_problem"] = period_problem
    result["replaces"] = _submitted(entity, year, number)
    return result


def _first_problems(result):
    """The file problems, then each row problem as "Line n: message", capped."""
    problems = list(result["file_problems"])
    for row in result["rows"]:
        problems.extend(f"Line {row['line']}: {p['message']}" for p in row["problems"])
    shown = "; ".join(problems[:_FIRST_PROBLEMS])
    more = len(problems) - _FIRST_PROBLEMS
    return shown + (f" (and {more} more)" if more > 0 else "")


def _restore_warehouse(old, new):
    """After a failure that followed the cancel: take the new batch's claim
    out (its document is rolled back) and claim the old batch again (its
    cancel is rolled back in MariaDB, but on_cancel's ClickHouse DELETE is not:
    Problems 3). Returns the steps that failed, as sentences; none is swallowed."""
    failed = []
    batch = new.get("batch_id") if new is not None else None
    if batch:
        try:
            execute(f"ALTER TABLE {CONTROL_TABLE} DELETE "
                    f"WHERE batch_id = '{_sql_str(batch)}' SETTINGS mutations_sync = 1")
        except Exception as e:   # noqa: BLE001 - reported below, never swallowed
            failed.append(f"the new batch {batch} may still be claimed ({e})")
    if old is not None:
        try:
            execute(_claim_insert([_claim_values(old, old.amount_basis)]))
        except Exception as e:   # noqa: BLE001 - reported below, never swallowed
            failed.append(f"{old.name} (batch {old.batch_id}) is not claimed in the warehouse ({e})")
    return failed


@frappe.whitelist(methods=["POST"])
def submit_tb(entity, fiscal_year, fiscal_period, amount_basis, content, replaces=None):
    """Submit a trial-balance CSV for one entity-period, replacing the one
    submitted there (``replaces``, the name ``check_tb`` returned) in one step.

    Order of work, and why:

    1. Refusals first, so a refusal writes nothing: the role and entity scope,
       a declared period of a type that takes trial balances and that is Open,
       the file (re-checked with the same rules as ``check_tb``), and the
       optimistic ``replaces`` check.
    2. Cancel the old TB. ``_check_no_other_submission`` refuses a second
       submitted TB, so the cancel must come before the new insert.
    3. Store the file privately, insert the new TB (``amended_from`` = the old
       one) and submit it. ``uploaded_on_behalf`` is set by the controller
       (A18), never from the request.
    4. If anything after the cancel raises, MariaDB rolls back but ClickHouse
       does not: the new batch's claim is deleted and the old batch claimed
       again, then the error propagates. If that repair itself fails the error
       says so and names what is left (Problems 3: a window remains while
       ClickHouse is down).

    Returns ``{name, replaced, on_behalf}``; ``on_behalf`` is None when the
    server recorded no value.
    """
    frappe.only_for(("EPM Admin", "Entity Accountant", "System Manager"))
    assert_entity_access(entity)
    if not frappe.db.exists("Entity", {"name": entity, "is_group": 0}):
        frappe.throw(f"Entity {entity} is not an entity a trial balance can be submitted for: "
                     "pick an existing entity that is not a group.")
    assert_postable(fiscal_year, fiscal_period)
    period = period_row(fiscal_year, fiscal_period)
    year, number = int(period["fiscal_year"]), int(period["fiscal_period"])
    assert_open(year, number, action="submit a trial balance")

    text = _text(content)
    try:
        rows = parse_tb_csv(text)
    except ValueError as e:
        frappe.throw(f"Could not read the trial balance file: {e}")
    result = check_rows(rows, chart_accounts(), entity, _partner_entities(rows),
                        amount_basis, BALANCE_TOLERANCE)
    if not result["ok"]:
        frappe.throw("The trial balance was not submitted: " + _first_problems(result))

    current = _submitted(entity, year, number)
    if (current or None) != (replaces or None):
        frappe.throw("The trial balance changed since you checked it; check the file again.")

    old = new = None
    try:
        if current:
            old = frappe.get_doc("Trial Balance Submission", current)
            old.cancel()
        file_doc = frappe.get_doc({
            "doctype": "File",
            "file_name": f"{entity}-FY{year}-P{number}.csv",
            "content": text,
            "is_private": 1,
        }).insert()
        new = frappe.get_doc({
            "doctype": "Trial Balance Submission",
            "data_area_id": entity,
            "fiscal_year": year,
            "fiscal_period": number,
            "amount_basis": amount_basis,
            "tb_file": file_doc.file_url,
            "amended_from": current or None,
        })
        new.insert()
        new.submit()
    except Exception as err:
        failed = _restore_warehouse(old, new)
        if failed:
            frappe.throw(f"The trial balance was not submitted ({err}), and restoring the "
                         f"warehouse failed: {'; '.join(failed)}. Ask the Close Lead to check "
                         "the claimed batches before the next build.")
        raise

    flag = new.get("uploaded_on_behalf")
    return {
        "name": new.name,
        "replaced": current or None,
        "on_behalf": {"Yes": True, "No": False}.get(flag),
    }

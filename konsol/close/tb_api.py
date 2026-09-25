"""Trial balance endpoints for the close app (konsol#305, story 3.2).

``check_tb`` judges a trial-balance CSV and persists nothing (D1). It is POST
only because a file does not fit in a query string: no File is uploaded, no
Trial Balance Upload or Submission is created, nothing is written to MariaDB or
ClickHouse. (``tb_bulk.check_file`` is not this pattern: it inserts a Trial
Balance Upload, and its client uploads a File first — Problems 2.)
"""

import frappe

from konsol.close.tb_model import check_rows
from konsol.consolidation.doctype.trial_balance_submission.trial_balance_submission import (
    BALANCE_TOLERANCE,
    PARTNER,
    parse_tb_csv,
)
from konsol.entity_permissions import assert_entity_access
from konsol.group_chart import chart_accounts
from konsol.period_status import OPEN, assert_postable, period_row

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

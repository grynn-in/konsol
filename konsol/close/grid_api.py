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
``group_rates.rate_gate`` and ``allowed_entity_codes``. Nothing is read per
entity, and nothing is written.

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
from konsol.close import period_grid_model, readiness_model, signoff_gate
from konsol.consolidation.doctype.assertion_run.assertion_run import latest_close_run
from konsol.entity_permissions import allowed_entity_codes
from konsol.period_status import PeriodNotDeclared

import importlib.util as _importlib_util
import os as _os


def _load_period_name():
    """konsol/close/period_name.py loaded by path (konsol#305 review-w5): the
    one "FY2025 P07" format, reachable even under the host tests' stub
    ``konsol.close`` package."""
    path = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "period_name.py")
    spec = _importlib_util.spec_from_file_location("konsol_close_period_name", path)
    module = _importlib_util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.period_name


period_name = _load_period_name()

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


@frappe.whitelist(methods=["GET"])
def get_period_grid(fiscal_year, fiscal_period):
    """``{period, rows, counts, rates_error}`` for a Regular period.

    ``period`` = ``{fiscal_year, fiscal_period, code, status, start_date}``
    (ISO date). ``rows``, ``counts`` and ``rates_error`` are
    ``period_grid_model.period_grid``'s. Read-only. Refuses an undeclared
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

    grid = period_grid_model.period_grid(
        key, rows, entities, ownership_rows, tbs, exceptions, rates, allowed)
    period = {"fiscal_year": key[0], "fiscal_period": key[1], "code": row.get("period_code"),
              "status": row.get("status"), "start_date": _iso(start)}
    return dict({"period": period}, **grid)


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

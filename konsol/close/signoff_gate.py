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

"""Forecast Horizon Coverage: does every period a forecast round's horizon
reaches have an approved Budget Cycle? (konsol#348 S6 KA9; QB10 b and S6-Q9 a,
taken under the standing rule: a Desk report over MariaDB, no warehouse read.)

A round is (scenario, as-of date) (Q23 a); a horizon that crosses the year end
is one Cycle per fiscal year sharing the as-of date (Q24 a). The rule, the
states and the sentences are ``horizon_coverage_model``'s; this report only
reads the inputs and lays out one row per round:

- forecast Budget Cycles, drafts and approved (``docstatus < 2``), through
  ``frappe.get_list`` so a user sees only the Cycles they may read, in an
  explicit order (v16 sorts by creation otherwise);
- the declared calendar, ``fiscal_calendar.fiscal_period_rows()`` (the EPM
  Fiscal Year periods the warehouse also reads).

Read-only (GET): nothing is written.
"""
import frappe

from konsol import fiscal_calendar
from konsol.epm import horizon_coverage_model as model

_FIELDS = ["name", "scenario_id", "scenario_type", "fiscal_year", "as_of_date", "horizon_periods", "docstatus"]

_STATE_LABELS = {
    model.OK: "Covered",
    model.UNCOVERED: "Uncovered",
    model.UNDECLARED_AS_OF: "As-of not declared",
}


def columns():
    return [
        {"fieldname": "scenario_id", "label": "Scenario", "fieldtype": "Link", "options": "Scenario", "width": 140},
        {"fieldname": "as_of_date", "label": "As Of", "fieldtype": "Date", "width": 100},
        {"fieldname": "as_of_period", "label": "As-of Period", "fieldtype": "Data", "width": 110},
        {"fieldname": "horizon_periods", "label": "Horizon (periods)", "fieldtype": "Int", "width": 90},
        {"fieldname": "pending_horizon_periods", "label": "Pending Horizon (draft)", "fieldtype": "Int", "width": 90},
        {"fieldname": "periods", "label": "Horizon Periods", "fieldtype": "Data", "width": 220},
        {"fieldname": "cycles", "label": "Cycles", "fieldtype": "Data", "width": 200},
        {"fieldname": "state", "label": "State", "fieldtype": "Data", "hidden": 1},
        {"fieldname": "state_label", "label": "Coverage", "fieldtype": "Data", "width": 120},
        {"fieldname": "sentence", "label": "Detail", "fieldtype": "Data", "width": 600},
    ]


def _period_text(period):
    return f"FY{period[0]} P{period[1]}" if period else ""


def rows(rounds):
    out = []
    for r in rounds:
        out.append({
            "scenario_id": r["scenario_id"],
            "as_of_date": r["as_of_date"],
            "as_of_period": _period_text(r["as_of_period"]),
            "horizon_periods": r["horizon_periods"],
            "pending_horizon_periods": r["pending_horizon_periods"],
            "periods": model.period_range_text(r["periods"]),
            "cycles": ", ".join(r["cycles"]),
            "state": r["state"],
            "state_label": _STATE_LABELS[r["state"]],
            "sentence": r["sentence"],
        })
    return out


def execute(filters=None):
    filters = frappe._dict(filters or {})
    cycle_filters = {"scenario_type": "forecast", "docstatus": ["<", 2]}
    if filters.get("scenario_id"):
        cycle_filters["scenario_id"] = filters.get("scenario_id")
    cycles = frappe.get_list(
        "Budget Cycle",
        filters=cycle_filters,
        fields=_FIELDS,
        order_by="scenario_id asc, as_of_date asc, fiscal_year asc, name asc",
        limit_page_length=0,
    )
    data = rows(model.rounds(cycles, fiscal_calendar.fiscal_period_rows()))
    message = None
    if not data:
        message = ("No forecast round to check: no draft or approved forecast Budget Cycle with an as-of date"
                   + (f" for {filters.get('scenario_id')}" if filters.get("scenario_id") else "") + ".")
    return columns(), data, message

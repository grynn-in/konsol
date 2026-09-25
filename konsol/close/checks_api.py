"""Checks endpoints for the close app (konsol#305 A26; stories 7.1-7.4).

``get_checks`` (GET) reads the period's Assertion Runs and passes them through
the pure A13 model (``konsol.close.checks_model``). ``run_checks`` (POST)
delegates to ``assertion_run.trigger_close_run``, which keeps its own role gate
and the one-run-at-a-time guard.

- The latest run is the newest Assertion Run for the period by ``creation``,
  whatever its status. The failures shown come from the latest *terminal* run
  (``latest_close_run``); ``results_run`` names it, so a screen can say the
  results are from an earlier run while a new one is in flight.
- Step fields are read by name and never include ``sample_rows`` (permlevel 1:
  the Analyst lacks it) or ``failures_table``.
- Descriptions are each dbt test's declared ``description`` in the manifest of
  the project the run tests. None is declared today (konsol#305 P4); the model
  marks them ``description_missing`` and no text is invented.
"""
from datetime import datetime

import frappe

from konsol.close.checks_model import by_cause, staleness
from konsol.close.freshness_api import current_freshness
from konsol.consolidation.doctype.assertion_run.assertion_run import (
    _manifest_nodes,
    latest_close_run,
    trigger_close_run,
)

#: Who may start the checks (R3, konsol#297); mirrors trigger_close_run's gate.
RUNNERS = ("EPM Admin", "EPM Analyst", "System Manager")
#: The Assertion Step fields the screen shows. Never ``sample_rows``.
STEP_FIELDS = ["assertion", "dimension", "status", "rows_failed", "severity", "message"]
#: The run's own fallback (assertion_run.run_close_assertions): descriptions
#: must come from the project the run actually tested.
DBT_PROJECT_FALLBACK = "/home/frappe/dbt_project"


def _period(fiscal_year, fiscal_period):
    try:
        return int(fiscal_year), int(fiscal_period)
    except (TypeError, ValueError):
        frappe.throw(f"FY{fiscal_year} P{fiscal_period} is not a period: "
                     "pass the fiscal year and period as whole numbers.")


def _iso(value):
    return value.isoformat() if hasattr(value, "isoformat") else value


def _latest(year, period):
    rows = frappe.get_all(
        "Assertion Run",
        filters={"fiscal_year": year, "fiscal_period": period},
        fields=["name", "status", "completed_at"],
        order_by="creation desc",
        limit=1,
    )
    return rows[0] if rows else None


def _steps(run_name):
    return frappe.get_all(
        "Assertion Step",
        filters={"parent": run_name, "parenttype": "Assertion Run"},
        fields=list(STEP_FIELDS),
        order_by="idx asc",
        limit_page_length=0,
    )


def _descriptions():
    project_path = frappe.get_single("EPM Settings").dbt_project_path or DBT_PROJECT_FALLBACK
    return {
        node["name"]: node.get("description")
        for node in _manifest_nodes(project_path).values()
        if node.get("resource_type") == "test" and node.get("name")
    }


@frappe.whitelist(methods=["GET"])
def get_checks(fiscal_year, fiscal_period):
    """The period's checks: ``{latest, staleness, staleness_note, as_of,
    results_run, domains, failures, warnings, can_run}``.

    ``latest`` is ``{name, status, completed_at}`` or None; ``staleness`` is
    ``not_run``, ``running``, ``stale`` or ``current`` (A13); ``domains`` is
    A13's ``by_cause`` for the latest terminal run.
    """
    frappe.only_for(("EPM Admin", "EPM Analyst", "Entity Accountant", "EPM User", "System Manager"))
    year, period = _period(fiscal_year, fiscal_period)

    latest = _latest(year, period)
    as_of = current_freshness()["as_of"]
    stale = staleness(latest, datetime.fromisoformat(as_of) if as_of else None)

    terminal = latest_close_run(year, period)
    results_run = terminal["name"] if terminal else None
    grouped = by_cause(_steps(results_run), _descriptions()) if results_run else {
        "domains": [], "failures": 0, "warnings": 0}

    return {
        "latest": ({"name": latest["name"], "status": latest["status"],
                    "completed_at": _iso(latest["completed_at"])} if latest else None),
        "staleness": stale["state"],
        "staleness_note": stale["note"],
        "as_of": as_of,
        "results_run": results_run,
        "domains": grouped["domains"],
        "failures": grouped["failures"],
        "warnings": grouped["warnings"],
        "can_run": bool(set(frappe.get_roles()) & set(RUNNERS)),
    }


@frappe.whitelist(methods=["POST"])
def run_checks(fiscal_year, fiscal_period):
    """Start the close checks for the period; returns the new Assertion Run's
    name. Refuses while another run is Queued or Running (trigger_close_run)."""
    frappe.only_for(("EPM Admin", "EPM Analyst", "System Manager"))
    year, period = _period(fiscal_year, fiscal_period)
    return trigger_close_run(year, period)

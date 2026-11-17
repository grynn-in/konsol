"""Consolidation Worksheet: the trial balance side by side, as a Desk report.

One row per account (a dimension optionally splits it), one column per
entity (its own layer and its CTA), then eliminations, adjustments and
Consolidated, for one root Consolidation Group and period. The numbers are
``tb_worksheet_api.get_worksheet``'s own (same roles, same scope, same
reads); this report only lays them out (``tb_worksheet_report_model``). A
non-ok worksheet (no group chosen, no chart, warehouse not built, setup gap,
error) is refused with its own message, never shown as an empty table.

Basis (konsol#348 S6 KB7; S6-Q5 a): ``Actual`` (the default, declared on the
filter) or ``Budget``, passed to ``get_worksheet``. On Budget, a state of
the approved budget (``BUDGET_STATES``, approved_budget_model) is shown as
the legend with no columns (BUD-4-2: a visible state, never a blank grid);
any other non-ok state is refused as on Actual.
"""
import frappe

from konsol.close import tb_worksheet_api, tb_worksheet_report_model
from konsol.consolidation.report.consolidated_statement.consolidated_statement import _group_code

#: approved_budget_model's states other than ok (Budget basis only).
BUDGET_STATES = ("no_approved_budget", "cycle_cancelled", "inputs_changed", "layer_missing")


def execute(filters=None):
    filters = frappe._dict(filters or {})
    if not filters.get("fiscal_year") or not filters.get("fiscal_period"):
        frappe.throw("Choose a fiscal year and a fiscal period.")

    group = _group_code(filters.get("consolidation_group"))
    payload = tb_worksheet_api.get_worksheet(
        filters.fiscal_year, filters.fiscal_period, group,
        entities=filters.get("entities") or None,
        dimension=filters.get("dimension") or None,
        basis=filters.get("basis") or "Actual")
    if payload.get("basis") == "Budget" and payload.get("state") in BUDGET_STATES:
        return [], [], tb_worksheet_report_model.budget_state_message(payload)
    if payload.get("state") != "ok":
        frappe.throw(payload.get("message") or "The worksheet could not be built.")

    ws = payload["worksheet"]
    currency = payload.get("reporting_currency")
    dimension = payload.get("dimension")
    try:
        columns = tb_worksheet_report_model.columns(ws, currency, payload.get("minor_unit"), dimension)
        data = tb_worksheet_report_model.rows(ws, currency, dimension)
        message = tb_worksheet_report_model.message(ws, payload)
    except ValueError as e:
        frappe.throw(str(e))
    return columns, data, message

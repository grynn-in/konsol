"""Consolidated Statement — the Close app's Numbers statement as a Desk report.

The consolidated Profit and Loss and Balance Sheet for one root
Consolidation Group and period. The numbers are ``statement_api``'s own
(``get_statement``: same roles, same reads, same signs); this report only
lays them out (``statement_report_model``). A non-ok statement (no group
chosen, no chart, warehouse not built, setup gap, error) is refused with
its own message, never shown as an empty table.

The Prior year column reads the same period and group a year earlier; a
prior year that is not available leaves the column blank and the report
message says why — it never fails the report.

The Budget columns (konsol#348 S5 K7) read the approved budget of the
statement's own group and year (``statement_api.budget_payload``), on the
Group basis only (the entity budget is S6). A budget that is not ok (no
approved budget, a cancelled Cycle, changed inputs, a missing layer, no
rows, a refused or faulty read) leaves the columns blank and the message
says why — it never fails the report either.
"""
import frappe

from konsol.close import statement_api, statement_report_model
# One doc-name -> code resolution (konsol#495 row 11b); the old name stays
# importable for the other reports that import it from here.
from konsol.close.statement_api import group_code as _group_code


def execute(filters=None):
    filters = frappe._dict(filters or {})
    if not filters.get("fiscal_year") or not filters.get("fiscal_period"):
        frappe.throw("Choose a fiscal year and a fiscal period.")

    if filters.get("basis") == "Entity":
        # Basis: Entity (konsol#495): one entity's own trial balance in its
        # functional currency. No group, so no group drill link.
        entity = filters.get("entity")
        if not entity:
            frappe.throw("Choose an entity.")
        payload = statement_api.get_entity_statement(filters.fiscal_year, filters.fiscal_period, entity)
        prior = _prior_year(filters.fiscal_year, filters.fiscal_period, None, entity=entity)
        drill_group = None
        budget = None
    else:
        group = _group_code(filters.get("consolidation_group"))
        payload = statement_api.get_statement(filters.fiscal_year, filters.fiscal_period, group)
        prior = _prior_year(filters.fiscal_year, filters.fiscal_period, group)
        drill_group = _drill_group(filters.get("consolidation_group"), payload)
        budget = _budget(filters.fiscal_year, filters.fiscal_period, payload)
    try:
        data = statement_report_model.rows(payload, prior, drill_group=drill_group, budget=budget)
        message = statement_report_model.message(payload, prior, budget=budget)
    except ValueError as e:
        frappe.throw(str(e))
    return statement_report_model.columns(payload["statement"]["minor_unit"]), data, message


def _drill_group(value, payload):
    """The Consolidation Group document name a heading's Statement Drill
    link carries: the filter's own value when one was chosen, else the root
    row of the group ``get_statement`` chose (the only group)."""
    if value:
        return value
    code = payload.get("consolidation_group")
    if not code:
        return None
    return frappe.db.get_value(
        "Consolidation Group", {"consolidation_group": code, "data_area_id": ["is", "not set"]}, "name")


def _prior_year(fiscal_year, fiscal_period, group, entity=None):
    """The same period and group a year earlier, for the Prior year column.
    A refused read (e.g. the prior year is not a declared period) becomes
    a non-ok payload carrying the refusal, and an ok one with no warehouse
    rows becomes ``no_rows`` (``prior_with_rows``): the report still
    renders, and its message says why the column is blank.

    ``_statement`` is ``get_statement`` without its role check, which the
    current-year ``get_statement`` call above has already passed; it also
    returns the TB rows the no-rows check needs."""
    try:
        if entity:
            prior, reads = statement_api._entity_statement(int(fiscal_year) - 1, fiscal_period, entity)
        else:
            prior, reads = statement_api._statement(int(fiscal_year) - 1, fiscal_period, group)
        return statement_report_model.prior_with_rows(prior, (reads or {}).get("tb_rows"))
    except frappe.ValidationError as e:
        # The refusal is shown in the report message, not as a dialog too.
        frappe.clear_last_message()
        return {"state": "error", "message": str(e), "statement": None}


def _budget(fiscal_year, fiscal_period, payload):
    """The approved budget's payload for the statement's own group (the
    group ``get_statement`` chose, so a blank filter still finds it), for
    the Budget columns (konsol#348 S5 K7). ``None`` when the statement is
    not ok (nothing to put a budget beside; the report refuses on the
    actuals' own message). A refused read (``frappe.throw``) or any fault
    in the read becomes a non-ok budget payload naming it, so the columns
    are blank with the reason in the legend and the actuals still render:
    a budget fault never fails the statement. A fault (anything but a
    refusal) is a bug, so it is also written to the Error Log with its
    traceback (S5 G2): the legend alone depends on a user reporting it.
    Nothing commits here; the request does."""
    if payload.get("state") != "ok" or not payload.get("consolidation_group"):
        return None
    try:
        return statement_api.budget_payload(fiscal_year, fiscal_period, payload["consolidation_group"])
    except frappe.ValidationError as e:
        frappe.clear_last_message()
        return {"state": "error", "message": str(e), "statement": None, "meta": {}}
    except Exception as e:  # noqa: BLE001 — visible in the legend, never a failed statement
        frappe.log_error(title="Consolidated Statement budget read", message=frappe.get_traceback())
        return {"state": "error", "message": f"{type(e).__name__}: {e}", "statement": None, "meta": {}}

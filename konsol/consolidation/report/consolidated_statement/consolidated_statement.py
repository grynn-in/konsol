"""Consolidated Statement — the Close app's Numbers statement as a Desk report.

The consolidated Profit and Loss and Balance Sheet for one root
Consolidation Group and period. The numbers are ``statement_api``'s own
(``get_statement``: same roles, same reads, same signs); this report only
lays them out (``statement_report_model``). A non-ok statement (no group
chosen, no chart, warehouse not built, setup gap, error) is refused with
its own message, never shown as an empty table.
"""
import frappe

from konsol.close import statement_api, statement_report_model


def execute(filters=None):
    filters = frappe._dict(filters or {})
    if not filters.get("fiscal_year") or not filters.get("fiscal_period"):
        frappe.throw("Choose a fiscal year and a fiscal period.")

    payload = statement_api.get_statement(
        filters.fiscal_year, filters.fiscal_period, _group_code(filters.get("consolidation_group")),
    )
    try:
        data = statement_report_model.rows(payload)
        message = statement_report_model.message(payload)
    except ValueError as e:
        frappe.throw(str(e))
    return statement_report_model.columns(), data, message


def _group_code(value):
    """The Link filter holds the Consolidation Group document name
    (``CG-<group>-``); ``get_statement`` takes the group code. A value that
    is not a document name is passed on as a code (``get_statement`` refuses
    one that is not a root group)."""
    if not value:
        return None
    code = frappe.db.get_value("Consolidation Group", value, "consolidation_group")
    return code or value

"""My Work — the Close app's My work list as a Desk report.

One row per item ``mywork_api.get_my_work`` returns for the caller (same
roles, same reads, same items), blocking first. This report only lays them
out (``mywork_report_model``), with an "Open" link to the nearest Desk
destination, never the Close app, and how to fix each one. It is the list
view of the My Work board (konsol#419; workspace "My Work"). Read-only.
"""
import frappe

from konsol.close import mywork_api, mywork_report_model


def execute(filters=None):
    payload = mywork_api.get_my_work()
    try:
        data = mywork_report_model.rows(payload)
    except ValueError as e:
        frappe.throw(str(e))
    return mywork_report_model.columns(), data, mywork_report_model.empty_message(payload, data)

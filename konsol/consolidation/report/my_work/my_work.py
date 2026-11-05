"""My Work — the Close app's My work list as a Desk report.

One row per item ``mywork_api.get_my_work`` returns for the caller (same
roles, same reads, same items), blocking first. This report only lays them
out (``mywork_report_model``), with an "Open" link to the nearest Desk
destination, never the Close app. Read-only.
"""
import frappe

from konsol.close import mywork_api, mywork_report_model


def execute(filters=None):
    payload = mywork_api.get_my_work()
    try:
        data = mywork_report_model.rows(payload)
    except ValueError as e:
        frappe.throw(str(e))
    # The Close app's own sentences (MyWork.vue, A54): only the Entity
    # Accountant persona carries entities_assigned (None for the others).
    message = None
    if payload.get("entities_assigned") is False:
        message = "No entities are assigned to you. Ask the System Manager."
    elif payload.get("entities_assigned") is True and not data:
        message = "Your entities are assigned, but none are in scope this period."
    return mywork_report_model.columns(), data, message

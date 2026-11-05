"""Approvals — the Close app's approvals queue as a Desk report.

One row per document waiting for the caller's approval across the 7
approval doctypes, then the ones sent back. The queue is
``approvals_api.get_queue``'s own (same roles, same reads, same approve
modes); this report only lays it out (``approvals_report_model``). The
Approve and Reject buttons (approvals.js) post to ``approval_api.approve``
and ``approval_api.reject``, exactly as the Close app does.
"""
import frappe

from konsol.close import approvals_api, approvals_report_model


def execute(filters=None):
    payload = approvals_api.get_queue()
    try:
        data = approvals_report_model.rows(payload)
        message = approvals_report_model.message(payload)
    except ValueError as e:
        frappe.throw(str(e))
    return approvals_report_model.columns(), data, message

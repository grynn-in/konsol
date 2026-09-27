"""The approve endpoint that carries the self-approval reason (konsol#305 P08,
#305-D2-3, R5).

``approve(doctype, name, reason=None)`` puts the reason on the request-scoped
flag the P07 hook reads (``self_approval.REASON_FLAG``), then approves: the
workflow "Approve" transition when the doctype has an active workflow,
otherwise ``doc.submit()``.

This endpoint does not decide policy. The ``before_submit`` hook
(``konsol.close.self_approval.check``) does: an "Allowed with reason"
self-approval with no reason, or any Blocked self-approval, is refused there.

The close-ui approval screen (E6) is the intended caller.
"""
import frappe

from konsol.close import close_policy_model
from konsol.close.self_approval import REASON_FLAG


@frappe.whitelist(methods=["POST"])
def approve(doctype, name, reason=None):
    frappe.only_for(("EPM Admin", "System Manager"))
    if doctype not in close_policy_model.APPROVAL_DOCTYPES:
        frappe.throw(
            "%s is not an approval document. Approve only one of: %s."
            % (doctype, ", ".join(close_policy_model.APPROVAL_DOCTYPES)))
    reason = (reason or "").strip()
    if reason:
        # A request-scoped flag, not doc.flags: apply_workflow reloads the doc.
        frappe.flags[REASON_FLAG] = {(doctype, name): reason}
    doc = frappe.get_doc(doctype, name)
    if frappe.db.get_value("Workflow", {"document_type": doctype, "is_active": 1}):
        from frappe.model.workflow import apply_workflow

        # Mirrors approve_adjustment (api.py:1819-1829). apply_workflow returns
        # None when it queues a background submission; read the doc back then.
        doc = apply_workflow(doc, "Approve") or frappe.get_doc(doctype, name)
    else:
        doc.submit()
    return {
        "name": doc.name,
        "docstatus": int(doc.docstatus),
        "self_approved": doc.owner == frappe.session.user,
    }

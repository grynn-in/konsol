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

Close Events (konsol#305 T02b, E10-P11): ``approve`` writes none itself. A
Desk submit and a workflow "Approve" never reach it, so the approval event is
written by the hook alone, once, for every submit path. ``reject`` is not a
submit, so it writes its own ``rejected`` event, in its own transaction; the
writer's exception is never caught.
"""
import frappe

from konsol.close import close_event, close_policy_model, self_approval
from konsol.close.self_approval import REASON_FLAG


#: konsol.consolidation.doctype.consolidation_journal.consolidation_journal
#: carries the same literal (named by string, like build_approval.py's own
#: BUILD_WRITER_FLAG): a test asserts the two stay in step.
REJECT_REASON_FLAG = "konsol_reject_reason"


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
    # The hook's own rule (#305-W2-14), read before the approve.
    self_approved = frappe.session.user in self_approval.preparers_for(
        doctype, {name: doc.owner})[name]
    if frappe.db.get_value("Workflow", {"document_type": doctype, "is_active": 1}):
        from frappe.model.workflow import apply_workflow

        # apply_workflow returns None when it queues a background submission;
        # read the doc back then.
        doc = apply_workflow(doc, "Approve") or frappe.get_doc(doctype, name)
    else:
        doc.submit()
    return {
        "name": doc.name,
        "docstatus": int(doc.docstatus),
        "self_approved": self_approved,
    }


@frappe.whitelist(methods=["POST"])
def reject(doctype, name, reason=None):
    """Reject a document awaiting approval, with a reason (konsol#305 J06a,
    #305-D2-8).

    With an active workflow, the document goes back to the workflow's first
    state. The Desk workflow bar's own Reject has no place for a reason, so a
    direct ``apply_workflow(doc, "Reject")`` is refused by the controller
    unless this request-scoped flag is set.

    With no active workflow (A04, #305-D2-8), there is no state to move: the
    draft stays as it is (no save, no docstatus change), and the reason is a
    Comment and a ``rejected`` Close Event. A submitted document is refused;
    its correction is a cancel and an amendment.

    Both paths return ``{"name", "docstatus", "status"}``.
    """
    frappe.only_for(("EPM Admin", "System Manager"))
    reason = (reason or "").strip()
    if not reason:
        frappe.throw("A rejection needs a reason.")
    if doctype not in close_policy_model.APPROVAL_DOCTYPES:
        frappe.throw(
            "%s is not an approval document, so it has nothing to reject "
            "(konsol#305-D2-8). Reject only one of: %s."
            % (doctype, ", ".join(close_policy_model.APPROVAL_DOCTYPES)))
    if not frappe.db.get_value("Workflow", {"document_type": doctype, "is_active": 1}):
        return _reject_draft(doctype, name, reason)
    # A request-scoped flag, not doc.flags: apply_workflow reloads the doc.
    frappe.flags[REJECT_REASON_FLAG] = {(doctype, name): reason}
    doc = frappe.get_doc(doctype, name)
    from frappe.model.workflow import apply_workflow

    doc = apply_workflow(doc, "Reject") or frappe.get_doc(doctype, name)
    doc.add_comment("Comment", f"Rejected: {reason}")
    close_event.record(
        "rejected", *close_event.period_of(doc), doctype, name, reason=reason,
        entity=close_event.entity_of(doc), detail={"preparer": doc.owner})
    return {"name": doc.name, "docstatus": int(doc.docstatus),
            "status": getattr(doc, "status", None)}


def _reject_draft(doctype, name, reason):
    """#305-D2-8: a doctype with no active workflow keeps its draft; the
    rejection is the Comment and the ``rejected`` event, in the request's
    transaction. The document itself is not saved."""
    doc = frappe.get_doc(doctype, name)
    if int(doc.docstatus) != 0:
        frappe.throw(
            "%s %s is already approved; a correction is a cancel and an amendment."
            % (doctype, name))
    # Read the period first (it raises for an undeclared period), so a refusal
    # leaves no Comment behind.
    period = close_event.period_of(doc)
    doc.add_comment("Comment", f"Rejected: {reason}")
    close_event.record(
        "rejected", *period, doctype, name, reason=reason,
        entity=close_event.entity_of(doc), detail={"preparer": doc.owner})
    return {"name": doc.name, "docstatus": 0, "status": None}

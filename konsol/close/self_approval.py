"""The self-approval hook (konsol#305-D2-3, R5), wired as
``doc_events["*"]["before_submit"]`` in hooks.py.

It acts only on ``close_policy_model.APPROVAL_DOCTYPES`` and applies
``close_policy_model.self_approval_problem`` to the submitting user and the
document's preparers: its owner, plus everyone who edited the draft
(#305-W2-14, ``preparers_for``). Frappe runs it on every submit path: the Desk
submit button, a workflow "Approve" (``apply_workflow`` ends in
``doc.submit()``), ``approval_api.approve`` and every programmatic submit.

Every user is held to the declared policy; there is no system-user exemption
(coordinator, 27 Sep). The only exemptions are the two named ones below.

Frappe's own ``allow_self_approval`` stays 1 on every workflow transition: it
cannot express "Allowed with reason", and 0 would also refuse the Analyst's own
"Send for Approval". This hook is the one rule.
"""
import frappe

from konsol.close import close_policy_model

REASON_FLAG = "konsol_self_approval_reason"


def _exempt(doc):
    """A named exemption, or None. The model honours any non-empty value."""
    if doc.doctype == "Ownership Period" and frappe.flags.get("from_business_combination"):
        # The Business Combination's approval is the approval; it submits its
        # Ownership Periods itself (business_combination.py:426-465).
        return "derived"
    if (frappe.flags.get("in_patch") or frappe.flags.get("in_install")
            or frappe.flags.get("in_migrate")):
        return "system"
    return None


def _reason(doc):
    # A request-scoped flag, not doc.flags: apply_workflow reloads the doc
    # (frappe/model/workflow.py:101-102). konsol.close.approval_api sets it.
    return (frappe.flags.get(REASON_FLAG) or {}).get((doc.doctype, doc.name))


def preparers_for(doctype, owners):
    """``{name: frozenset}`` of who prepared each document (#305-W2-14).

    ``owners`` is ``{name: owner}``. Two reads, whatever the number of names:
    the documents' Versions (``get_all`` ignores permissions, so an approver
    without Version read is still judged) and the doctype's active workflow
    state field (None without a workflow). Empty ``owners`` reads nothing.
    """
    if not owners:
        return {}
    rows = frappe.get_all(
        "Version",
        filters={"ref_doctype": doctype, "docname": ["in", sorted(owners)]},
        fields=["docname", "owner", "data"],
        order_by="creation asc",
        limit_page_length=0,
    )
    state_field = frappe.db.get_value(
        "Workflow", {"document_type": doctype, "is_active": 1}, "workflow_state_field")
    by_name = {}
    for row in rows:
        by_name.setdefault(row["docname"], []).append(row)
    return {
        name: close_policy_model.preparers(owner, by_name.get(name, ()), state_field)
        for name, owner in owners.items()
    }


def check(doc, method=None):
    if doc.doctype not in close_policy_model.APPROVAL_DOCTYPES:
        return
    user = frappe.session.user
    preparers = preparers_for(doc.doctype, {doc.name: doc.owner})[doc.name]
    if user not in preparers:
        return
    exempt = _exempt(doc)
    if exempt:
        return
    policy = frappe.db.get_single_value("Close Settings", "self_approval")
    reason = _reason(doc)
    problem = close_policy_model.self_approval_problem(
        policy, preparers, user, doc.doctype, doc.name, reason, exempt)
    if problem:
        frappe.throw(problem, frappe.PermissionError)
    doc.add_comment("Comment", close_policy_model.self_approval_note(
        policy, user, reason.strip()))

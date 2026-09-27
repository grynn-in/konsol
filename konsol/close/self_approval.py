"""The self-approval hook (konsol#305-D2-3, R5), wired as
``doc_events["*"]["before_submit"]`` in hooks.py.

It acts only on ``close_policy_model.APPROVAL_DOCTYPES`` and applies
``close_policy_model.self_approval_problem`` to the submitting user and the
document's owner (its preparer). Frappe runs it on every submit path: the Desk
submit button, a workflow "Approve" (``apply_workflow`` ends in
``doc.submit()``), ``approve_adjustment`` and every programmatic submit.

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


def check(doc, method=None):
    if doc.doctype not in close_policy_model.APPROVAL_DOCTYPES:
        return
    user = frappe.session.user
    if user != doc.owner:
        return
    exempt = _exempt(doc)
    if exempt:
        return
    policy = frappe.db.get_single_value("Close Settings", "self_approval")
    reason = _reason(doc)
    problem = close_policy_model.self_approval_problem(
        policy, doc.owner, user, doc.doctype, doc.name, reason, exempt)
    if problem:
        frappe.throw(problem, frappe.PermissionError)
    doc.add_comment("Comment", close_policy_model.self_approval_note(
        policy, user, reason.strip()))

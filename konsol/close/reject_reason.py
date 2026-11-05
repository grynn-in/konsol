"""A workflow Reject must carry a reason (konsol#310).

``konsol.close.approval_api.reject`` asks for the reason, records it as a
Comment and a ``rejected`` Close Event, and sets ``REJECT_REASON_FLAG`` around
its own ``apply_workflow(doc, "Reject")``. Any other Reject (the workflow
bar, the REST API, a script calling apply_workflow) sets no flag, so it
records no reason, and this refuses it.

Business Combination and Business Disposal call ``refuse_reasonless_reject``
first in ``validate``. Consolidation Journal has its own copy of the rule
(``ConsolidationJournal._validate_reject_reason``, konsol#305 J06a), which
also covers a journal with no workflow.

The states are the active workflow's, never fixed names: workflows are
installed once and a site may rename them. With no active workflow there is
no state to move: ``approval_api._reject_draft`` leaves the draft unsaved, so
there is nothing to refuse here.
"""
import frappe
from frappe.model.workflow import get_workflow_name
from frappe.utils import cint

#: konsol.close.approval_api sets the same literal; a test keeps them in step.
REJECT_REASON_FLAG = "konsol_reject_reason"


def refuse_reasonless_reject(doc):
    """Refuse a save of ``doc`` that moves it from a later draft state back to
    the workflow's first state without approval_api.reject's flag for it."""
    if cint(doc.docstatus) != 0:
        return
    name = get_workflow_name(doc.doctype)
    if not name:
        return
    workflow = frappe.get_cached_doc("Workflow", name)
    field = workflow.workflow_state_field
    first = workflow.states[0].state
    drafts = {s.state for s in workflow.states if cint(s.doc_status) == 0}
    if doc.get(field) != first:
        return
    before = doc.get_doc_before_save()
    before_state = getattr(before, field, None) if before else None
    if before_state is None or before_state == first or before_state not in drafts:
        return
    if frappe.flags.get(REJECT_REASON_FLAG, {}).get((doc.doctype, doc.name)):
        return
    frappe.throw(frappe._(
        "Reject this {0} with a reason: use Actions → Reject on the form, "
        "or konsol.close.approval_api.reject."
    ).format(doc.doctype))

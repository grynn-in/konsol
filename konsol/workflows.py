"""Install the app's workflows once: create if missing, never overwrite.

Decided 12 Sep 2026: workflows are installed once so a site may customise them.
A patch can't do that job, because a fresh install marks every patch as done
without running it (frappe/installer.py), and a customer reset is a fresh
install. So this runs from after_install (fresh sites, before fixtures) and
from after_migrate (existing sites). It is idempotent, and it skips a doctype
that already has ANY workflow: a site's own workflow stays, and inserting ours
as active would deactivate it.

Definitions live beside their doctype as `<doctype>_workflow.json`. Only the
doctypes in INSTALLED get theirs: a workflow changes how a doctype is used, so
each is switched on deliberately. allocation_run_workflow.json is dormant.
Build Approval's system transitions are taken by the build job as Administrator.
"""
import glob
import json
import os

import frappe

INSTALLED = ("Consolidation Journal", "Business Combination", "Business Disposal", "Build Approval",
             "Budget Cycle", "Cost Allocation Run", "Cost Allocation Statistic", "Rate Sheet")


def _definitions():
    root = frappe.get_app_path("konsol")
    for path in sorted(glob.glob(os.path.join(root, "*", "doctype", "*", "*_workflow.json"))):
        with open(path) as f:
            yield json.load(f)


def install_workflows():
    installed = []
    for wf in _definitions():
        doctype = wf["document_type"]
        if doctype not in INSTALLED or not frappe.db.exists("DocType", doctype):
            continue
        if frappe.db.exists("Workflow", {"document_type": doctype}):
            continue
        for state in wf["states"]:
            if not frappe.db.exists("Workflow State", state["state"]):
                frappe.get_doc({"doctype": "Workflow State",
                                "workflow_state_name": state["state"]}).insert(ignore_permissions=True)
        for transition in wf["transitions"]:
            if not frappe.db.exists("Workflow Action Master", transition["action"]):
                frappe.get_doc({"doctype": "Workflow Action Master",
                                "workflow_action_name": transition["action"]}).insert(ignore_permissions=True)
        frappe.get_doc(dict(wf, workflow_name=wf.get("workflow_name") or wf["name"])).insert(ignore_permissions=True)
        installed.append(wf["workflow_name"])
    return installed


# -- the cancelled state (konsol#529) -----------------------------------------
#
# Frappe's plain Cancel sets docstatus 2 and nothing else, and no shipped
# workflow has a transition into its docstatus-2 state, so a cancelled document
# kept "Approved". Chosen on #529 under the standing rule, option (b): the
# controller's before_cancel calls set_cancelled_state(self). The state is read
# from the active Workflow as the site has it, the way budget_intake.cycle_workflow
# reads the first state: a site may rename the states (12 Sep 2026, point 3), so
# no state name is written here. Frappe's _cancel() is a save: before_cancel runs
# in it before db_update() writes every field, so the value persists with no
# second save. Warehouse decisions still read docstatus.

def cancelled_states(states):
    """The docstatus-2 state names of a workflow, in its order (a list, maybe
    empty). ``states`` are (state, doc_status) pairs; doc_status is the
    Workflow Document State's string ("2") or an int."""
    return [state for state, doc_status in states or () if str(doc_status) == "2"]


def cancel_target(doctype):
    """``(state_field, docstatus_2_states)`` for the active Workflow on
    ``doctype`` as installed, or None when no workflow is active or it has no
    docstatus-2 state (Build Approval's shape). A blank Workflow State Field
    is Frappe's default, ``workflow_state``."""
    name = frappe.db.get_value("Workflow", {"document_type": doctype, "is_active": 1}, "name")
    if not name:
        return None
    workflow = frappe.get_cached_doc("Workflow", name)
    states = cancelled_states((s.state, s.doc_status) for s in workflow.states or ())
    if not states:
        return None
    return (workflow.workflow_state_field or "workflow_state", states)


def set_cancelled_state(doc):
    """Call from ``before_cancel``: put the workflow's docstatus-2 state in the
    document's state field, unless it already holds one (apply_workflow sets
    the transition's next state before it calls ``doc.cancel()``; with two
    docstatus-2 states the transition's choice stands). Returns the state the
    field holds afterwards, or None when there was nothing to set."""
    target = cancel_target(doc.doctype)
    if not target:
        return None
    field, states = target
    current = getattr(doc, field, None)
    if current in states:
        return current
    setattr(doc, field, states[0])
    return states[0]

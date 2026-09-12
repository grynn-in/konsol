"""Install the app's workflows once: create if missing, never overwrite.

Decided 12 Sep 2026: workflows are installed once so a site may customise them.
A patch can't do that job, because a fresh install marks every patch as done
without running it (frappe/installer.py), and the Ecolab reset is a fresh
install. So this runs from after_install (fresh sites, before fixtures) and
from after_migrate (existing sites). It is idempotent, and it skips a doctype
that already has ANY workflow: a site's own workflow stays, and inserting ours
as active would deactivate it.

Definitions live beside their doctype as `<doctype>_workflow.json`. Only the
doctypes in INSTALLED get theirs: a workflow changes how a doctype is used, so
each is switched on deliberately. allocation_run_workflow.json is dormant.
"""
import glob
import json
import os

import frappe

INSTALLED = ("Consolidation Adjustment",)


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

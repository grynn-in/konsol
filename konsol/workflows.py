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

#: The roles an earlier release shipped in each workflow. A site whose
#: workflow still carries exactly these, on exactly our states and
#: transitions, never customised it, so it takes the current definition's
#: roles. Anything else is the site's own choice and is left alone.
#: Consolidation Adjustment shipped as System Manager only until F7
#: (12 Sep 2026), when drafting went to EPM Analyst and approval to EPM Admin.
PREVIOUSLY_SHIPPED_ROLES = {
    "Consolidation Adjustment": frozenset({"System Manager"}),
}


def planned_role_upgrade(states, transitions, definition, previous_roles):
    """The role changes to make to an installed workflow, or None.

    ``states`` is [(state, allow_edit)], ``transitions`` is
    [(state, action, allowed)], both as installed. Pure, so it is testable
    without a site.
    """
    if not previous_roles:
        return None
    installed_roles = {r for _, r in states} | {r for _, _, r in transitions}
    if installed_roles != set(previous_roles):
        return None
    edit = {s["state"]: s["allow_edit"] for s in definition["states"]}
    allowed = {(t["state"], t["action"]): t["allowed"] for t in definition["transitions"]}
    if {s for s, _ in states} != set(edit) or {(s, a) for s, a, _ in transitions} != set(allowed):
        return None
    if all(edit[s] == r for s, r in states) and all(allowed[(s, a)] == r for s, a, r in transitions):
        return None
    return edit, allowed


def _upgrade_roles(definition):
    doctype = definition["document_type"]
    name = frappe.db.get_value(
        "Workflow",
        {"document_type": doctype, "workflow_name": definition.get("workflow_name") or definition["name"]},
        "name",
    )
    if not name:
        return None
    wf = frappe.get_doc("Workflow", name)
    plan = planned_role_upgrade(
        [(s.state, s.allow_edit) for s in wf.states],
        [(t.state, t.action, t.allowed) for t in wf.transitions],
        definition,
        PREVIOUSLY_SHIPPED_ROLES.get(doctype),
    )
    if not plan:
        return None
    edit, allowed = plan
    for s in wf.states:
        s.allow_edit = edit[s.state]
    for t in wf.transitions:
        t.allowed = allowed[(t.state, t.action)]
    wf.save(ignore_permissions=True)
    new_roles = set(edit.values()) | set(allowed.values())
    return {"workflow": wf.name, "granted": _grant_previous_approvers(PREVIOUSLY_SHIPPED_ROLES[doctype], new_roles)}


def _grant_previous_approvers(previous_roles, new_roles):
    """Give the people who could act under the old roles the new ones.

    Without this, the upgrade silently takes approval away from everyone who
    approved yesterday (they held System Manager, not EPM Admin). Runs once,
    because the upgrade itself runs once. Administrator already holds every
    role. Returns {user: [roles granted]} so the migrate can say so.
    """
    holders = set(frappe.get_all("Has Role", filters={"parenttype": "User", "role": ["in", sorted(previous_roles)]},
                                 pluck="parent")) - {"Administrator", "Guest"}
    if not holders:
        return {}
    granted = {}
    for user in frappe.get_all("User", filters={"name": ["in", sorted(holders)], "enabled": 1}, pluck="name"):
        missing = sorted(set(new_roles) - set(frappe.get_roles(user)))
        if missing:
            frappe.get_doc("User", user).add_roles(*missing)
            granted[user] = missing
    return granted


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
            upgraded = _upgrade_roles(wf)
            if upgraded:
                installed.append(f"{wf['workflow_name']} (roles upgraded)")
                for user, roles in upgraded["granted"].items():
                    installed.append(f"{user} given {', '.join(roles)} so they keep the access they had")
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

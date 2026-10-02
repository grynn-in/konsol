"""konsol#305 L01 cleanup: undo close_w2_setup.py.

Run INSIDE the backend container, from the sites directory, after the setup:

    docker cp scripts/close_w2_cleanup.py konsolidat_backend:/home/frappe/frappe-bench/
    docker exec -u frappe konsolidat_backend bash -lc \
        'cd /home/frappe/frappe-bench/sites && ../env/bin/python ../close_w2_cleanup.py'

It reads ``zz_w2_state.json`` and deletes exactly the users and User
Permissions recorded there, by name (never by pattern, prompt rule 9), plus
the rows the walk's logins left for those exact users (Sessions, Activity
Log, Access Log, Route History) and the Version / Deleted Document rows of
the deleted documents created since the setup started.

Build Approvals numbered above the setup's mark are listed; a Pending Review
one is cancelled by the workflow's Reject, never deleted; rows at or below
the mark are never touched. It deletes the password file and the state file
last, and prints the counts before setup, before cleanup and after.
"""
import json
import os

import frappe

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, "zz_w2_state.json")
PW_FILE = os.path.join(HERE, "zz_w2_pw")
COUNTED = ("User", "User Permission", "Build Approval", "Close Event", "Group Exchange Rate",
           "Business Combination", "Consolidation Journal")


def counts():
    return {dt: frappe.db.count(dt) for dt in COUNTED}


def name_number(name):
    tail = str(name).rsplit("-", 1)[-1]
    return int(tail) if "-" in str(name) and tail.isdigit() else None


def settle_builds(mark):
    from frappe.model.workflow import apply_workflow, get_transitions

    out = []
    rows = frappe.get_all("Build Approval", fields=["name", "workflow_state", "build_scope",
                                                    "trigger_doctype", "trigger_docname"],
                          limit_page_length=0)
    new = sorted((r for r in rows if (name_number(r.name) or 0) > mark["number"]),
                 key=lambda r: name_number(r.name))
    for row in new:
        outcome = row.workflow_state
        if row.workflow_state == "Pending Review":
            doc = frappe.get_doc("Build Approval", row.name)
            if any(t.action == "Reject" for t in get_transitions(doc)):
                apply_workflow(doc, "Reject")
                frappe.db.commit()
                outcome = "cancelled (workflow Reject)"
            else:
                outcome = "LEFT PENDING: no Reject offered to Administrator"
        out.append((row.name, row.workflow_state, row.build_scope,
                    f"{row.trigger_doctype} {row.trigger_docname}", outcome))
    return out


def main():
    frappe.init(site="konsolidat.local", sites_path="/home/frappe/frappe-bench/sites")
    frappe.connect()
    frappe.set_user("Administrator")
    state = json.load(open(STATE))
    created = state["created"]
    since = state["started_at"]
    before = counts()

    deleted = []
    for name in created.get("user_permissions", []):
        if frappe.db.exists("User Permission", name):
            frappe.delete_doc("User Permission", name, force=1, ignore_permissions=True)
            deleted.append(("User Permission", name))
    users = list(created.get("users", []))
    for u in users:
        if frappe.db.exists("User", u):
            frappe.delete_doc("User", u, force=1, ignore_permissions=True)
            deleted.append(("User", u))
    if users:
        ph = ", ".join(["%s"] * len(users))
        for table, col in (("tabSessions", "user"), ("tabActivity Log", "user"),
                           ("tabAccess Log", "user"), ("tabRoute History", "user")):
            frappe.db.sql(f"delete from `{table}` where `{col}` in ({ph})", users)
    frappe.db.commit()

    # The audit rows of what was deleted (deleting a user also deletes its
    # Notification Settings, named after the user).
    for dt, name in deleted:
        frappe.db.sql("delete from tabVersion where ref_doctype=%s and docname=%s and creation >= %s",
                      (dt, name, since))
        frappe.db.sql("delete from `tabDeleted Document` where deleted_name=%s and creation >= %s",
                      (name, since))
    frappe.db.commit()
    frappe.clear_cache()

    builds = settle_builds(state["build_approval_mark"])
    after = counts()

    for path in (PW_FILE, STATE):
        if os.path.exists(path):
            os.remove(path)

    print("cleanup done")
    print("  deleted:", deleted)
    print("  build approvals above mark", state["build_approval_mark"]["name"], ":", builds or "none")
    print(f"  {'doctype':<24} {'before setup':>12} {'before cleanup':>15} {'after cleanup':>14}")
    for dt in COUNTED:
        print(f"  {dt:<24} {state['counts_before'][dt]:>12} {before[dt]:>15} {after[dt]:>14}")
    left = {
        "users": [u for u in users if frappe.db.exists("User", u)],
        "user_permissions": [n for n in created.get("user_permissions", [])
                             if frappe.db.exists("User Permission", n)],
        "sessions": frappe.db.sql(
            "select count(*) from tabSessions where user in ({})".format(
                ", ".join(["%s"] * len(users))), users)[0][0] if users else 0,
    }
    print("  recorded names left:", left)


if __name__ == "__main__":
    main()

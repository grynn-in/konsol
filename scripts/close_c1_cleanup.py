"""konsol#305 C1 cleanup: undo close_c1_setup.py and the walk-through.

Run INSIDE the backend container, from the sites directory, after the setup:

    docker cp scripts/close_c1_cleanup.py konsolidat_backend:/home/frappe/frappe-bench/
    docker exec -u frappe konsolidat_backend bash -lc \
        'cd /home/frappe/frappe-bench/sites && ../env/bin/python ../close_c1_cleanup.py'

It reads ``zz_c1_state.json`` (written by the setup) and removes only what the
setup or the walk-through created:
- FY2099 did not exist before the setup (the setup refuses otherwise), so
  every Trial Balance Submission, TB Exception, Group Exchange Rate and
  Assertion Run in FY2099 is ours: 2099 periods are reopened, then those are
  cancelled and deleted (Files attached to the deleted TBs too);
- the zz-c1 users, their User Permission, ZZOP, its node and Ownership Period;
- FY2099 itself;
- Build Approvals created since the setup started are cancelled (workflow
  Reject/Cancel), never deleted;
- the recorded reporting_frequency values and the Close Settings tabSingles
  rows are put back exactly;
- ``konsol.clickhouse.reconcile_all()`` re-syncs the warehouse.
It prints a before/after count of every touched doctype and the ZZ rows left.
"""
import json
import os

import frappe

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, "zz_c1_state.json")
FY = 2099
ENTITY = "ZZOP"
REASON = "C1 cleanup"
COUNTED = ("EPM Fiscal Year", "Entity", "Consolidation Group", "Ownership Period", "TB Exception",
           "Trial Balance Submission", "Group Exchange Rate", "Assertion Run", "Pipeline Run",
           "User", "User Permission", "Build Approval", "File", "Trial Balance Upload")


def counts():
    return {dt: frappe.db.count(dt) for dt in COUNTED}


def zz_left():
    return {
        "FY2099": frappe.db.count("EPM Fiscal Year", {"fiscal_year": FY}),
        "FY2099 TBs": frappe.db.count("Trial Balance Submission", {"fiscal_year": FY}),
        "FY2099 TB Exceptions": frappe.db.count("TB Exception", {"fiscal_year": FY}),
        "FY2099 rates": frappe.db.count("Group Exchange Rate", {"fiscal_year": FY}),
        "FY2099 runs": frappe.db.count("Assertion Run", {"fiscal_year": FY}),
        "ZZ entities": frappe.db.count("Entity", {"name": ["like", "ZZ%"]}),
        "ZZ nodes": frappe.db.count("Consolidation Group", {"data_area_id": ["like", "ZZ%"]}),
        "ZZ ownership": frappe.db.count("Ownership Period", {"data_area_id": ["like", "ZZ%"]}),
        "zz users": frappe.db.count("User", {"name": ["like", "zz-%"]}),
        "zz user permissions": frappe.db.count("User Permission", {"user": ["like", "zz-%"]}),
    }


#: (doctype, name) of every document this script deletes: their Version,
#: Comment and Deleted Document rows go too, so no ZZ data stays behind.
DELETED = []


def _cancel_delete(doctype, name):
    DELETED.append((doctype, name))
    doc = frappe.get_doc(doctype, name)
    if doc.docstatus == 1:
        doc.cancel()
    frappe.delete_doc(doctype, name, force=1, ignore_permissions=True)


def main():
    frappe.init(site="konsolidat.local", sites_path="/home/frappe/frappe-bench/sites")
    frappe.connect()
    frappe.set_user("Administrator")
    state = json.load(open(STATE))
    before = counts()
    left_before = zz_left()
    created = state["created"]

    # Reopen every closed 2099 period (cancel needs an open period).
    fy_name = created.get("fiscal_year") or frappe.db.get_value("EPM Fiscal Year", {"fiscal_year": FY})
    if fy_name and frappe.db.exists("EPM Fiscal Year", fy_name):
        fy = frappe.get_doc("EPM Fiscal Year", fy_name)
        if fy.status != "Open":
            fy.reopen_year(REASON)
            frappe.db.commit()
            fy = frappe.get_doc("EPM Fiscal Year", fy_name)
        for row in sorted(fy.periods, key=lambda r: -int(r.fiscal_period)):
            if row.status != "Open":
                fy.reopen_period(row.fiscal_period, REASON)
                frappe.db.commit()
                fy = frappe.get_doc("EPM Fiscal Year", fy_name)

    # FY2099 documents: all ours (the setup refused a pre-existing FY2099).
    tb_names = frappe.get_all("Trial Balance Submission", filters={"fiscal_year": FY}, pluck="name",
                              order_by="creation desc", limit_page_length=0)
    tb_files = frappe.get_all("File", filters={"attached_to_doctype": "Trial Balance Submission",
                                               "attached_to_name": ["in", tb_names or ["-"]]},
                              pluck="name", limit_page_length=0)
    for name in tb_names:
        _cancel_delete("Trial Balance Submission", name)
    for name in tb_files:
        DELETED.append(("File", name))
        if frappe.db.exists("File", name):
            frappe.delete_doc("File", name, force=1, ignore_permissions=True)
    frappe.db.commit()
    for name in frappe.get_all("TB Exception", filters={"fiscal_year": FY}, pluck="name", limit_page_length=0):
        _cancel_delete("TB Exception", name)
    frappe.db.commit()
    for name in frappe.get_all("Group Exchange Rate", filters={"fiscal_year": FY}, pluck="name",
                               limit_page_length=0):
        _cancel_delete("Group Exchange Rate", name)
    frappe.db.commit()
    # A finished or signed run refuses deletion (A51) by design; test data is
    # removed underneath the controller, with its result rows.
    runs = frappe.get_all("Assertion Run", filters={"fiscal_year": FY}, pluck="name", limit_page_length=0)
    for name in runs:
        DELETED.append(("Assertion Run", name))
        frappe.db.delete("Assertion Step", {"parent": name, "parenttype": "Assertion Run"})
        frappe.db.delete("Assertion Run", {"name": name})
    frappe.db.commit()

    # Users.
    for name in created.get("user_permissions", []):
        DELETED.append(("User Permission", name))
        if frappe.db.exists("User Permission", name):
            frappe.delete_doc("User Permission", name, force=1, ignore_permissions=True)
    users = [u for u in created.get("users", []) if u.startswith("zz-c1-")]
    for u in users:
        DELETED.append(("User", u))
        if frappe.db.exists("User", u):
            frappe.delete_doc("User", u, force=1, ignore_permissions=True)
    if users:
        ph = ", ".join(["%s"] * len(users))
        for table, col in (("tabSessions", "user"), ("tabActivity Log", "user"), ("tabAccess Log", "user"),
                           ("tabRoute History", "user")):
            frappe.db.sql(f"delete from `{table}` where `{col}` in ({ph})", users)
    frappe.db.commit()

    # ZZOP, its ownership, its node.
    if created.get("ownership_period") and frappe.db.exists("Ownership Period", created["ownership_period"]):
        _cancel_delete("Ownership Period", created["ownership_period"])
    for dt, key in (("Consolidation Group", "cg_node"), ("Entity", "entity")):
        if created.get(key):
            DELETED.append((dt, created[key]))
    if created.get("cg_node") and frappe.db.exists("Consolidation Group", created["cg_node"]):
        frappe.delete_doc("Consolidation Group", created["cg_node"], force=1, ignore_permissions=True)
    if created.get("entity") == ENTITY and frappe.db.exists("Entity", ENTITY):
        frappe.delete_doc("Entity", ENTITY, force=1, ignore_permissions=True)
    frappe.db.commit()

    # FY2099.
    if fy_name:
        DELETED.append(("EPM Fiscal Year", fy_name))
    if fy_name and frappe.db.exists("EPM Fiscal Year", fy_name):
        frappe.delete_doc("EPM Fiscal Year", fy_name, force=1, ignore_permissions=True)
    frappe.db.commit()

    # The audit rows of what was deleted, and of the Close Settings edit.
    for dt, name in DELETED + [("Close Settings", "Close Settings")]:
        since = state["started_at"]
        frappe.db.sql("delete from tabVersion where ref_doctype=%s and docname=%s and creation >= %s",
                      (dt, name, since))
        frappe.db.sql("delete from tabComment where reference_doctype=%s and reference_name=%s "
                      "and creation >= %s", (dt, name, since))
        frappe.db.sql("delete from `tabDeleted Document` where deleted_doctype=%s and deleted_name=%s "
                      "and creation >= %s", (dt, name, since))
    frappe.db.commit()

    # Deleting a user deletes its Notification Settings, which leaves a
    # Deleted Document named after the user.
    for u in users:
        frappe.db.sql("delete from `tabDeleted Document` where deleted_name=%s and creation >= %s",
                      (u, state["started_at"]))
    frappe.db.commit()

    # Recorded values back exactly.
    for name, prior in state.get("frequency_set", {}).items():
        frappe.db.set_value("Entity", name, "reporting_frequency", prior["reporting_frequency"],
                            update_modified=False)
    frappe.db.sql("delete from tabSingles where doctype='Close Settings'")
    for field, value in state["close_settings_singles"]:
        frappe.db.sql("insert into tabSingles (doctype, field, value) values ('Close Settings', %s, %s)",
                      (field, value))
    frappe.db.commit()

    # Build Approvals the walk-through caused: cancelled, not deleted.
    from frappe.model.workflow import apply_workflow, get_transitions
    new_bas = [n for n in frappe.get_all("Build Approval", pluck="name", limit_page_length=0)
               if n not in set(state["build_approvals_before"])]
    ba_result = {}
    for name in new_bas:
        doc = frappe.get_doc("Build Approval", name)
        acts = [t.action for t in get_transitions(doc) if "ancel" in t.action or "eject" in t.action]
        if acts:
            apply_workflow(doc, acts[0])
            frappe.db.commit()
        ba_result[name] = frappe.db.get_value("Build Approval", name, "workflow_state")

    frappe.clear_cache()
    from konsol import clickhouse
    clickhouse.reconcile_all()
    frappe.db.commit()

    # Warehouse rows ZZOP left behind: the landed TB rows (unclaimed once the
    # TB is cancelled, reaped only after REAP_AFTER_DAYS) and the hierarchy row
    # the node's staging build wrote (dropped only at the next build).
    ch_left = {}
    for table in ("epm_raw.trial_balance_submissions", "epm_raw.trial_balance_submission_control",
                  "epm_gold.gold_consolidation_hierarchy"):
        clickhouse.execute(f"ALTER TABLE {table} DELETE WHERE data_area_id = '{ENTITY}' "
                           "SETTINGS mutations_sync = 1")
        ch_left[table] = int(clickhouse.execute(
            f"SELECT count() FROM {table} WHERE data_area_id = '{ENTITY}'").strip())

    after = counts()
    freq_ok = all(frappe.db.get_value("Entity", n, "reporting_frequency") == p["reporting_frequency"]
                  and str(frappe.db.get_value("Entity", n, "modified")) == p["modified"]
                  for n, p in state.get("frequency_set", {}).items())
    singles = [list(r) for r in frappe.db.sql(
        "select field, value from tabSingles where doctype='Close Settings' order by field")]
    print("cleanup done")
    print("  build approvals created since setup:", ba_result)
    print("  frequencies restored (value and modified):", freq_ok, len(state.get("frequency_set", {})))
    print("  close settings singles restored:", singles == state["close_settings_singles"], singles)
    print(f"  {'doctype':<26} {'before setup':>12} {'before cleanup':>15} {'after cleanup':>14}")
    for dt in COUNTED:
        print(f"  {dt:<26} {state['counts_before'][dt]:>12} {before[dt]:>15} {after[dt]:>14}")
    print("  ZZ rows before cleanup:", left_before)
    left = zz_left()
    print("  ZZ rows left:", left, "total", sum(left.values()))
    print("  warehouse ZZOP rows left:", ch_left)


if __name__ == "__main__":
    main()

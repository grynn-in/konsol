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
- every Build Approval and Pipeline Run numbered above the marks the setup
  recorded is listed with its status and trigger, after the queued build
  requests drain (up to WAIT_SECONDS). A Pending Review build is cancelled by
  the workflow's Reject, never deleted; a Completed one is printed as "left on
  live (cannot be cancelled)". Records at or below the marks are never touched;
  pre-existing open builds are printed as not touched;
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


#: Build Approval states that are not terminal (tasks.request_build_for_scope).
OPEN_BUILD_STATES = ("Draft", "Pending Review", "Approved", "Running")
#: The job id queue_consolidation_build gives every build request (tasks.py).
BUILD_REQUEST_JOB = "konsol-build-request::"
WAIT_SECONDS = 300


def name_number(name):
    """BAPR-00061 -> 61; None for a name that is not <prefix>-<digits>."""
    tail = str(name).rsplit("-", 1)[-1]
    return int(tail) if "-" in str(name) and tail.isdigit() else None


def above_mark(doctype, mark, fields):
    """Rows of ``doctype`` numbered above the setup's mark, oldest first.
    Rows at or below it existed before the setup and are never touched."""
    rows = frappe.get_all(doctype, fields=["name"] + fields, limit_page_length=0)
    odd = [r.name for r in rows if name_number(r.name) is None]
    if odd:
        print(f"  WARNING {doctype} names outside the naming series, not touched: {odd}")
    new = [r for r in rows if name_number(r.name) is not None and name_number(r.name) > mark["number"]]
    return sorted(new, key=lambda r: name_number(r.name))


def pending_build_requests():
    """Queued or running build-request jobs of this site (every queue)."""
    from frappe.utils.background_jobs import get_queues
    from rq.registry import StartedJobRegistry

    prefix = f"{frappe.local.site}::{BUILD_REQUEST_JOB}"
    ids = []
    for q in get_queues():
        # Read the started registry's key directly: its get_job_ids() runs
        # RQ's cleanup first, which moves abandoned jobs to the failed
        # registry, a write this listing must not make.
        started = q.connection.zrange(StartedJobRegistry(queue=q).key, 0, -1)
        ids += q.get_job_ids() + [i.decode() if isinstance(i, bytes) else i for i in started]
    return sorted(i for i in ids if i.startswith(prefix))


def open_builds(mark):
    return [r.name for r in above_mark("Build Approval", mark, ["workflow_state"])
            if r.workflow_state in ("Draft", "Approved", "Running")]


def wait_for_build_requests(mark):
    """Wait until no build request is queued or running, and no build above
    the mark is Draft, Approved or Running (a staging build finishing may
    request a follow-up). Returns True, or the list still waiting after
    WAIT_SECONDS: it is printed, never hidden."""
    import time

    deadline = time.time() + WAIT_SECONDS
    while True:
        frappe.db.rollback()  # a fresh snapshot of the rows the worker wrote
        waiting = pending_build_requests() + open_builds(mark)
        if not waiting:
            return True
        if time.time() > deadline:
            return waiting
        time.sleep(5)


def _trigger(row):
    gone = row.trigger_doctype and row.trigger_docname and not frappe.db.exists(
        row.trigger_doctype, row.trigger_docname)
    return f"{row.trigger_doctype} {row.trigger_docname}" + (" (gone)" if gone else "")


def settle_builds(mark):
    """Every Build Approval above the mark: a Pending Review one is cancelled
    by the workflow's own Reject (Pending Review -> Cancelled, the only
    cancel path in build_approval_workflow.json); none is deleted."""
    from frappe.model.workflow import apply_workflow, get_transitions

    out = []
    fields = ["workflow_state", "build_scope", "trigger_doctype", "trigger_docname"]
    for row in above_mark("Build Approval", mark, fields):
        item = {"name": row.name, "before": row.workflow_state, "scope": row.build_scope,
                "trigger": _trigger(row)}
        if row.workflow_state == "Pending Review":
            doc = frappe.get_doc("Build Approval", row.name)
            if any(t.action == "Reject" for t in get_transitions(doc)):
                apply_workflow(doc, "Reject")
                frappe.db.commit()
                item["outcome"] = "cancelled (workflow Reject)"
            else:
                item["outcome"] = "LEFT PENDING: the workflow offers no Reject to Administrator"
        elif row.workflow_state == "Cancelled":
            item["outcome"] = "already cancelled"
        elif row.workflow_state in ("Completed", "Failed"):
            item["outcome"] = "left on live (cannot be cancelled)"
        else:
            item["outcome"] = f"LEFT {row.workflow_state.upper()}: still in flight after {WAIT_SECONDS}s"
        item["after"] = frappe.db.get_value("Build Approval", row.name, "workflow_state")
        out.append(item)
    return out


def runs_since(mark):
    return [dict(r) for r in above_mark("Pipeline Run", mark, ["status", "build_approval", "triggered_by"])]


def main():
    frappe.init(site="konsolidat.local", sites_path="/home/frappe/frappe-bench/sites")
    frappe.connect()
    frappe.set_user("Administrator")
    state = json.load(open(STATE))
    missing = [k for k in ("build_approval_mark", "pipeline_run_mark", "open_build_approvals_before")
               if k not in state]
    if missing:
        raise SystemExit(f"REFUSED, nothing changed: {STATE} has no {missing}; it was written by a "
                         "setup older than C1b, so the builds this run caused cannot be told apart")
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

    # Build Approvals and Pipeline Runs the setup, walk-through and this
    # cleanup caused (C1b). Last, after every change: a change queues its
    # build request as a job, so the request lands after the save.
    waited = wait_for_build_requests(state["build_approval_mark"])
    builds = settle_builds(state["build_approval_mark"])
    runs_after = runs_since(state["pipeline_run_mark"])

    after = counts()
    freq_ok = all(frappe.db.get_value("Entity", n, "reporting_frequency") == p["reporting_frequency"]
                  and str(frappe.db.get_value("Entity", n, "modified")) == p["modified"]
                  for n, p in state.get("frequency_set", {}).items())
    singles = [list(r) for r in frappe.db.sql(
        "select field, value from tabSingles where doctype='Close Settings' order by field")]
    print("cleanup done")
    print(f"  build approvals above the mark {state['build_approval_mark']['name']} "
          f"(build-request jobs drained: {waited}):")
    for b in builds:
        print(f"    {b['name']} {b['before']} -> {b['after']} scope={b['scope']} "
              f"trigger={b['trigger']}: {b['outcome']}")
    print(f"  pipeline runs above the mark {state['pipeline_run_mark']['name']}:")
    for r in runs_after:
        print(f"    {r['name']} status={r['status']} build_approval={r['build_approval']} "
              f"triggered_by={r['triggered_by']}")
    still_open = [b["name"] for b in builds if b["after"] in OPEN_BUILD_STATES]
    print("  builds left pending or in flight:", still_open or "none")
    pre = frappe.get_all("Build Approval",
                         filters={"name": ["in", [b["name"] for b in state["open_build_approvals_before"]] or ["-"]]},
                         fields=["name", "workflow_state"], order_by="name asc")
    print("  pre-existing open builds (not touched):", [(b.name, b.workflow_state) for b in pre] or "none")
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

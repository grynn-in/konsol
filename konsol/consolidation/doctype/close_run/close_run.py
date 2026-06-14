"""Close Run — runs the dbt close assertion suite and records each result.

Mirrors the frappe/press build-state pattern: a parent doc with a child table
(`Assertion Result`) of per-check rows, a status Select rendered as a colored
indicator, and a streamed log via frappe.publish_realtime.
"""
import json
import os
import subprocess

import frappe
from frappe.model.document import Document


class CloseRun(Document):
    pass


# --- dimension classification (filename/keyword -> bucket) ---------------
def _classify(name):
    n = name.lower()
    if "ownership" in n:
        return "Ownership"
    if any(k in n for k in ("alloc", "step", "tier", "pool", "reciprocal", "driver", "active")):
        return "Allocation"
    if any(k in n for k in ("cta", "rate", "currency", "fx")):
        return "FX"
    if any(k in n for k in ("ic_", "consolidat", "nci", "equity", "elimination",
                            "fctb", "balance", "bs_", "end_to_end")):
        return "Consolidation"
    if any(k in n for k in ("null", "schema", "chart", "unique", "valid")):
        return "Data Quality"
    return "Other"


_DBT_TO_STATUS = {"pass": "Pass", "fail": "Fail", "error": "Error"}


@frappe.whitelist()
def trigger_close_run(fiscal_year=None, fiscal_period=None):
    """Create a Close Run and enqueue the assertion suite.

    Refuses to start if another run is already Queued/Running — only one
    assertion suite may run at a time (concurrent `dbt test` would contend on
    the warehouse and produce confusing interleaved state).
    """
    active = frappe.db.get_value("Close Run", {"status": ["in", ("Queued", "Running")]}, "name")
    if active:
        frappe.throw(
            frappe._("A close run is already in progress: {0}. Wait for it to finish before starting another.").format(active),
            title=frappe._("Run already in progress"),
        )

    doc = frappe.get_doc(
        {
            "doctype": "Close Run",
            "status": "Queued",
            "fiscal_year": fiscal_year,
            "fiscal_period": fiscal_period,
            "triggered_by": frappe.session.user,
            "title": frappe.utils.now(),
        }
    )
    doc.insert(ignore_permissions=True)
    frappe.db.commit()
    frappe.enqueue(
        "konsol.consolidation.doctype.close_run.close_run.run_close_assertions",
        queue="long",
        timeout=900,
        close_run=doc.name,
    )
    return doc.name


def _emit(name, **payload):
    payload["run"] = name
    frappe.publish_realtime("close_run_update", payload, doctype="Close Run", docname=name)


def run_close_assertions(close_run):
    """Background job: run `dbt test` for the singular assertions, stream the
    log, then parse run_results.json into Assertion Result rows."""
    doc = frappe.get_doc("Close Run", close_run)
    doc.status = "Running"
    doc.started_at = frappe.utils.now_datetime()
    doc.log = ""
    doc.save(ignore_permissions=True)
    frappe.db.commit()

    project_path = frappe.get_single("EPM Settings").dbt_project_path or "/home/frappe/dbt_project"
    cmd = [
        "dbt", "test",
        "--select", "test_type:singular",
        "--store-failures",
        "--project-dir", project_path,
        "--profiles-dir", project_path,
    ]

    lines = []
    try:
        proc = subprocess.Popen(
            cmd, cwd=project_path, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True, bufsize=1,
        )
        for raw in proc.stdout:
            line = raw.rstrip("\n")
            lines.append(line)
            _emit(close_run, line=line)
            if len(lines) % 20 == 0:  # checkpoint the log periodically
                frappe.db.set_value("Close Run", close_run, "log", "\n".join(lines)[-20000:],
                                    update_modified=False)
                frappe.db.commit()
        proc.wait(timeout=900)
    except subprocess.TimeoutExpired:
        proc.kill()
        lines.append("ERROR: assertion run timed out after 900s")
    except Exception as e:  # noqa: BLE001
        lines.append(f"ERROR: {e}")

    doc.reload()
    doc.log = "\n".join(lines)[-20000:]
    _parse_results(doc, project_path)

    doc.completed_at = frappe.utils.now_datetime()
    if doc.started_at and doc.completed_at:
        doc.duration_seconds = round((doc.completed_at - doc.started_at).total_seconds(), 1)
    doc.save(ignore_permissions=True)
    frappe.db.commit()
    _emit(close_run, done=True, status=doc.status,
          passed=doc.passed, failed=doc.failed, errored=doc.errored)


def _parse_results(doc, project_path):
    """Read target/run_results.json into the results child table."""
    rr_path = os.path.join(project_path, "target", "run_results.json")
    doc.set("results", [])
    passed = failed = errored = 0

    try:
        with open(rr_path) as fh:
            rr = json.load(fh)
    except Exception as e:  # noqa: BLE001
        doc.status = "Error"
        doc.append("results", {"assertion": "run_results.json", "status": "Error",
                               "dimension": "Other", "message": f"could not read results: {e}"})
        doc.total, doc.passed, doc.failed, doc.errored = 1, 0, 0, 1
        return

    for node in rr.get("results", []):
        uid = node.get("unique_id", "")
        # unique_id looks like: test.open_epm.assert_xxx.<hash>
        name = uid.split(".")[2] if len(uid.split(".")) > 2 else uid
        raw_status = (node.get("status") or "").lower()
        status = _DBT_TO_STATUS.get(raw_status, "Error")
        failures = node.get("failures") or 0
        if status == "Pass":
            passed += 1
        elif status == "Fail":
            failed += 1
        else:
            errored += 1
        doc.append("results", {
            "assertion": name,
            "dimension": _classify(name),
            "status": status,
            "rows_failed": failures,
            "severity": "error",
            "message": (node.get("message") or "")[:280],
            "failures_table": (node.get("relation_name") or "").strip('`"') if status == "Fail" else "",
        })

    doc.total = passed + failed + errored
    doc.passed, doc.failed, doc.errored = passed, failed, errored
    doc.status = "Green" if (failed == 0 and errored == 0 and doc.total > 0) else "Red"

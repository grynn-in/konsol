"""Period Close — runs the dbt close assertion suite and records each result.

Mirrors the frappe/press build-state pattern: a parent doc with a child table
(`Assertion Result`) of per-check rows, a status Select rendered as a colored
indicator, and a streamed log via frappe.publish_realtime.
"""
import json
import os
import subprocess

import frappe
from frappe.model.document import Document


class PeriodClose(Document):
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


def _dbt_bin():
    """Absolute path to the dbt binary in the bench venv.

    The web/worker processes don't have the venv's bin on PATH, so a bare
    `dbt` raises FileNotFoundError. Fall back to `dbt` only if the venv copy
    isn't present.
    """
    candidate = os.path.join(frappe.utils.get_bench_path(), "env", "bin", "dbt")
    return candidate if os.path.exists(candidate) else "dbt"


@frappe.whitelist()
def trigger_close_run(fiscal_year=None, fiscal_period=None):
    """Create a Period Close and enqueue the assertion suite.

    Refuses to start if another run is already Queued/Running — only one
    assertion suite may run at a time (concurrent `dbt test` would contend on
    the warehouse and produce confusing interleaved state).
    """
    active = frappe.db.get_value("Period Close", {"status": ["in", ("Queued", "Running")]}, "name")
    if active:
        frappe.throw(
            frappe._("A close run is already in progress: {0}. Wait for it to finish before starting another.").format(active),
            title=frappe._("Run already in progress"),
        )

    doc = frappe.get_doc(
        {
            "doctype": "Period Close",
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
        "konsol.consolidation.doctype.period_close.period_close.run_close_assertions",
        queue="long",
        timeout=900,
        close_run=doc.name,
    )
    return doc.name


# Runs older than this with no terminal status are treated as dead. Kept above
# the 900s job timeout so a legitimately-long run is never reaped mid-flight.
STALE_MINUTES = 20


def reap_stale_close_runs():
    """Scheduled: mark long-stuck Period Closes as Error.

    The concurrency guard in trigger_close_run() blocks new runs while one is
    Queued/Running. If a worker dies mid-run, that record would stay Running
    forever and wedge the guard permanently. This sweep releases it.
    """
    cutoff = frappe.utils.add_to_date(frappe.utils.now_datetime(), minutes=-STALE_MINUTES)
    stale = frappe.get_all(
        "Period Close",
        filters={"status": ["in", ("Queued", "Running")], "modified": ["<", cutoff]},
        pluck="name",
    )
    for name in stale:
        prev = frappe.db.get_value("Period Close", name, "log") or ""
        frappe.db.set_value(
            "Period Close", name,
            {"status": "Error",
             "log": f"{prev}\n[reaper] marked Error: no progress for >{STALE_MINUTES}m"},
            update_modified=False,
        )
    if stale:
        frappe.db.commit()
        frappe.logger().info(f"Period Close reaper: marked {len(stale)} stale run(s) Error: {stale}")
    return stale


def _emit(name, **payload):
    payload["run"] = name
    frappe.publish_realtime("close_run_update", payload, doctype="Period Close", docname=name)


# --- Sign-off gate (PRD §6.10 §4) ----------------------------------------
# A close is "signed off" only via sign_off_close(): a Green run signs off
# cleanly; a Red/Error run is blocked unless an EPM Admin supplies a reason
# (audited as an Overridden sign-off). Queued/Running can't be signed off.
OVERRIDE_ROLES = {"System Manager", "EPM Admin"}
TERMINAL_STATUSES = ("Green", "Red", "Error")
SIGNED_STATES = ("Signed Off", "Overridden")


def latest_close_run(fiscal_year, fiscal_period):
    """Most recent terminal Period Close for a period, or None.

    Ordered by completion time (not creation): a run is created Queued and only
    later becomes terminal, so a re-run that finishes later is authoritative.
    """
    rows = frappe.get_all(
        "Period Close",
        filters={"fiscal_year": fiscal_year, "fiscal_period": fiscal_period,
                 "status": ["in", TERMINAL_STATUSES]},
        fields=["name", "status", "signoff_status", "failed", "errored"],
        order_by="completed_at desc, creation desc",
        limit=1,
    )
    return rows[0] if rows else None


def _failed_assertion_names(close_run, limit=10):
    return frappe.get_all(
        "Assertion Result",
        filters={"parent": close_run, "status": ["in", ("Fail", "Error")]},
        pluck="assertion",
        limit=limit,
    )


@frappe.whitelist()
def sign_off_close(close_run, override_reason=None):
    """Sign off a Period Close — the reconciliation gate.

    Green  -> signed off (caller must have write on Period Close).
    Red/Error -> BLOCKED, unless the caller is an EPM Admin / System Manager AND
                 supplies a reason -> recorded as an audited "Overridden" sign-off.
    Queued/Running -> rejected (run not finished).
    """
    # Enforce write access BEFORE we switch to ignore_permissions for the save
    # (the sign-off fields are read_only, so the save itself must bypass perms).
    frappe.has_permission("Period Close", "write", doc=close_run, throw=True)

    # Row lock so two concurrent sign-offs can't both pass the idempotency check.
    frappe.db.get_value("Period Close", close_run, "name", for_update=True)
    doc = frappe.get_doc("Period Close", close_run)

    if doc.signoff_status in SIGNED_STATES:
        frappe.throw(
            frappe._("Period Close {0} is already {1}.").format(close_run, doc.signoff_status),
            title=frappe._("Already signed off"))

    if doc.status in ("Queued", "Running"):
        frappe.throw(
            frappe._("Period Close {0} is still {1} — wait for it to finish before signing off.")
            .format(close_run, doc.status))

    if doc.status == "Green":
        new_state = "Signed Off"
        reason = None
    else:
        # Red or Error — gated override: require an override role FIRST, then a reason.
        if not (OVERRIDE_ROLES & set(frappe.get_roles())):
            frappe.throw(
                frappe._("Only an EPM Admin may override a {0} close sign-off.").format(doc.status),
                exc=frappe.PermissionError, title=frappe._("Sign-off blocked"))
        reason = (override_reason or "").strip()
        if not reason:
            failing = ", ".join(_failed_assertion_names(close_run)) or "(see results)"
            frappe.throw(
                frappe._("Close is not reconciled (status {0}). Failing assertions: {1}. "
                         "Provide a reason to override.").format(doc.status, failing),
                title=frappe._("Override reason required"))
        new_state = "Overridden"

    doc.signoff_status = new_state
    doc.signed_off_by = frappe.session.user
    doc.signed_off_at = frappe.utils.now_datetime()
    doc.override_reason = reason
    doc.save(ignore_permissions=True)
    frappe.db.commit()
    return {"signoff_status": new_state, "signed_off_by": doc.signed_off_by}


def assert_close_signed_off(fiscal_year, fiscal_period):
    """Gate hook: raise unless the period's latest Period Close is signed off
    (Green) or audited-overridden.

    NOTE: this is the integration point for the budget/consolidation approval
    chain (PRD §6.5). §6.5 is not built yet, so there is no caller in this PR —
    wire `assert_close_signed_off(year, period)` into the approval transition
    when §6.5 lands. The sign-off *action* itself is already gated by
    sign_off_close() above.
    """
    run = latest_close_run(fiscal_year, fiscal_period)
    if not run:
        frappe.throw(
            frappe._("No completed Period Close for {0}-{1}. Run the close assertion suite before sign-off.")
            .format(fiscal_year, fiscal_period),
            title=frappe._("Close not asserted"))
    if run.signoff_status not in SIGNED_STATES:
        failing = ", ".join(_failed_assertion_names(run.name)) or "(see results)"
        frappe.throw(
            frappe._("Close {0}-{1} is not signed off (run {2}, status {3}). Failing: {4}.")
            .format(fiscal_year, fiscal_period, run.name, run.status, failing),
            title=frappe._("Close sign-off required"))
    return run.name


def run_close_assertions(close_run):
    """Background job: run `dbt test` for the singular assertions, stream the
    log, then parse run_results.json into Assertion Result rows."""
    doc = frappe.get_doc("Period Close", close_run)
    doc.status = "Running"
    doc.started_at = frappe.utils.now_datetime()
    doc.log = ""
    doc.save(ignore_permissions=True)
    frappe.db.commit()

    project_path = frappe.get_single("EPM Settings").dbt_project_path or "/home/frappe/dbt_project"
    cmd = [
        _dbt_bin(), "test",
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
                frappe.db.set_value("Period Close", close_run, "log", "\n".join(lines)[-20000:],
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


def _fetch_failure_sample(relation, limit=20):
    """Fetch up to `limit` offending rows from a --store-failures table.

    `relation` is dbt's relation_name, e.g. `epm_dbt_test__audit`.`assert_x`.
    Returns an aligned text table (ClickHouse PrettyCompact) for display in the
    Assertion Result, or a short note on failure — never raises.
    """
    rel = relation.replace("`", "").strip()
    if not rel:
        return ""
    try:
        from konsol.clickhouse import execute
        return execute(f"SELECT * FROM {rel} LIMIT {int(limit)} FORMAT PrettyCompactNoEscapes")[:8000]
    except Exception as e:  # noqa: BLE001
        return f"(could not fetch sample from {rel}: {e})"


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

        relation = (node.get("relation_name") or "") if status == "Fail" else ""
        doc.append("results", {
            "assertion": name,
            "dimension": _classify(name),
            "status": status,
            "rows_failed": failures,
            "severity": "error",
            "message": (node.get("message") or "")[:280],
            "failures_table": relation.replace("`", ""),
            "sample_rows": _fetch_failure_sample(relation) if relation else "",
        })

    doc.total = passed + failed + errored
    doc.passed, doc.failed, doc.errored = passed, failed, errored
    doc.status = "Green" if (failed == 0 and errored == 0 and doc.total > 0) else "Red"

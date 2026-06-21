"""Background jobs: governed dbt builds + Airbyte pipeline.

Build governance: doc saves create Pipeline Build Requests instead of
firing raw `dbt build`. Scopes map to dbt domain tags for selective builds.
"""
import os
import subprocess
import time

import frappe
import requests


def _dbt_bin():
    """Absolute path to the bench-venv dbt binary.

    Web/worker processes don't have env/bin on PATH, so a bare `dbt` raises
    FileNotFoundError. Fall back to `dbt` only if the venv copy is absent.
    """
    candidate = os.path.join(frappe.utils.get_bench_path(), "env", "bin", "dbt")
    return candidate if os.path.exists(candidate) else "dbt"


# ---------------------------------------------------------------------------
# Build scope mapping: doctype → (scope, risk)
# ---------------------------------------------------------------------------
# All 9 trigger doctypes map to "staging" (low risk) by default.
# Full/actuals/consolidation rebuilds require manual Pipeline Build Request.
DOCTYPE_BUILD_MAP = {
    "Consolidation Group": {"scope": "staging", "risk": "low"},
    "Consolidation Adjustment": {"scope": "staging", "risk": "low"},
    "Ownership Period": {"scope": "staging", "risk": "low"},
    "Historical Equity Rate": {"scope": "staging", "risk": "low"},
    "IC Elimination Rule": {"scope": "staging", "risk": "low"},
    "IC Balance": {"scope": "staging", "risk": "low"},
    "Allocation Rule": {"scope": "staging", "risk": "low"},
    "Allocation Driver": {"scope": "staging", "risk": "low"},
    "Allocation Run": {"scope": "staging", "risk": "low"},
}

# Scope → dbt selector. Kept as the fallback/default; the Build Domain doctype
# is the runtime source of truth (see _scope_selector / _raw_dependent_scopes).
SCOPE_SELECTOR = {
    "staging": "tag:domain:staging",
    "actuals": "tag:domain:actuals",
    "scenarios": "tag:domain:scenarios",
    "consolidation": "tag:domain:consolidation",
    "reporting": "+tag:domain:reporting",
    "full": None,  # no selector = full build
}

# Scopes that require epm_raw data (fallback; see _raw_dependent_scopes).
RAW_DEPENDENT_SCOPES = {"actuals", "scenarios", "consolidation", "reporting", "full"}


def _known_domains():
    """Per-model build domains. Prefers the Build Domain doctype; falls back to
    the hardcoded SCOPE_SELECTOR domains (excluding the special 'full' scope)."""
    try:
        if frappe.db.table_exists("Build Domain"):
            names = frappe.get_all("Build Domain", pluck="name")
            if names:
                return set(names)
    except Exception:
        pass
    return {s for s, sel in SCOPE_SELECTOR.items() if sel is not None}


def _scope_selector(scope):
    """dbt --select for a build scope; None for 'full' or an unknown scope
    (no selector → full build), preserving the original SCOPE_SELECTOR semantics."""
    if scope in _known_domains():
        return f"tag:domain:{scope}"
    return None


def _raw_dependent_scopes():
    """Scopes that require epm_raw. Prefers Build Domain docs flagged
    requires_raw_data=1 (plus the 'full' build); falls back to RAW_DEPENDENT_SCOPES."""
    try:
        if frappe.db.table_exists("Build Domain"):
            rows = frappe.get_all(
                "Build Domain", filters={"requires_raw_data": 1}, pluck="name")
            if rows:
                return set(rows) | {"full"}
    except Exception:
        pass
    return set(RAW_DEPENDENT_SCOPES)


# ---------------------------------------------------------------------------
# Preflight check
# ---------------------------------------------------------------------------
def _preflight_check(build_scope):
    """Validate preconditions before running a build.

    Returns (ok: bool, message: str).
    - staging scope always passes (no epm_raw dependency)
    - Raw-dependent scopes check Airbyte sync status in EPM Settings
    """
    from konsol.clickhouse import check_health

    # Check ClickHouse connectivity
    ch_status = check_health()
    if ch_status["status"] != "healthy":
        return False, f"ClickHouse unhealthy: {ch_status}"

    # Staging doesn't need raw data
    if build_scope == "staging":
        return True, "Staging scope — no raw data dependency"

    # Raw-dependent scopes check Airbyte sync status
    if build_scope in _raw_dependent_scopes():
        return check_raw_data_available()

    return True, "OK"


def check_raw_data_available():
    """Check if epm_raw has valid data.

    When connectors are registered, gate on per-connector sync status (an
    enabled connector that has never synced or whose last sync Failed/Running
    blocks the build, and the message names it). Otherwise fall back to the
    global EPM Settings Airbyte sync status.

    Returns (ok: bool, message: str).
    """
    if frappe.db.table_exists("Connector"):
        connectors = frappe.get_all(
            "Connector",
            filters={"enabled": 1},
            fields=["name", "connector_name", "last_sync_status", "last_sync_at"],
            limit_page_length=0,
        )
        if connectors:
            for c in connectors:
                if not c.last_sync_at:
                    return False, f"Connector '{c.connector_name}' has never synced — epm_raw may be empty"
                if c.last_sync_status in ("Failed", "Running"):
                    return False, f"Connector '{c.connector_name}' sync status is '{c.last_sync_status}' — cannot build from raw"
            return True, f"All {len(connectors)} enabled connectors synced OK"

    settings = frappe.get_single("EPM Settings")

    sync_status = settings.last_airbyte_sync_status
    sync_at = settings.last_airbyte_sync_at

    if not sync_at:
        return False, "Airbyte has never synced — epm_raw may be empty"

    if sync_status in ("Failed", "Running"):
        return False, f"Airbyte sync status is '{sync_status}' — cannot build from raw"

    return True, f"Airbyte sync OK (status={sync_status}, rows={settings.last_airbyte_sync_rows})"


# ---------------------------------------------------------------------------
# Governed dbt build (called from Pipeline Build Request)
# ---------------------------------------------------------------------------
def run_governed_build(build_request):
    """Execute a governed dbt build for a Pipeline Build Request.

    Called via frappe.enqueue from PipelineBuildRequest.on_update.
    Runs preflight checks, then selective dbt build with --select tag.
    """
    doc = frappe.get_doc("Pipeline Build Request", build_request)
    doc.workflow_state = "Running"
    doc.started_at = frappe.utils.now_datetime()
    doc.save(ignore_permissions=True)
    frappe.db.commit()

    try:
        # Preflight
        ok, msg = _preflight_check(doc.build_scope)
        doc.preflight_result = msg
        if not ok:
            doc.workflow_state = "Failed"
            doc.error_message = f"Preflight failed: {msg}"
            doc.completed_at = frappe.utils.now_datetime()
            _set_duration(doc)
            doc.save(ignore_permissions=True)
            frappe.db.commit()
            return

        # Build dbt command
        settings = frappe.get_single("EPM Settings")
        project_path = settings.dbt_project_path
        cmd = [_dbt_bin(), "build", "--project-dir", project_path, "--profiles-dir", project_path]

        selector = _scope_selector(doc.build_scope)
        if selector:
            cmd.extend(["--select", selector])

        # Execute
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=300,
            cwd=project_path,
        )
        output = result.stdout + "\n" + result.stderr
        doc.build_output = output[-5000:]  # cap at 5K chars

        if result.returncode == 0:
            doc.workflow_state = "Completed"
        else:
            doc.workflow_state = "Failed"
            doc.error_message = f"dbt build failed (rc={result.returncode})"

    except subprocess.TimeoutExpired:
        doc.workflow_state = "Failed"
        doc.error_message = "dbt build timed out after 300s"
    except Exception as e:
        doc.workflow_state = "Failed"
        doc.error_message = str(e)

    doc.completed_at = frappe.utils.now_datetime()
    _set_duration(doc)
    doc.save(ignore_permissions=True)
    frappe.db.commit()

    frappe.publish_realtime(
        "build_request_complete",
        {
            "name": doc.name,
            "state": doc.workflow_state,
            "scope": doc.build_scope,
            "duration": doc.duration_seconds,
        },
    )


def _set_duration(doc):
    """Calculate duration_seconds from started_at to completed_at."""
    if doc.started_at and doc.completed_at:
        delta = doc.completed_at - doc.started_at
        doc.duration_seconds = round(delta.total_seconds(), 1)


# ---------------------------------------------------------------------------
# Hook: trigger governed build after consolidation/allocation doc changes
# ---------------------------------------------------------------------------
def on_consolidation_doc_update(doc, method):
    """Called by doc_events hook for consolidation/allocation doctypes.

    Creates a Pipeline Build Request instead of firing raw dbt build.
    Uses DOCTYPE_BUILD_MAP to determine scope and risk level.
    """
    # Inert during app install / migrate / fixture import: loading fixtures
    # (e.g. allocation_rule.json) must not enqueue pipeline builds — and
    # ClickHouse credentials (EPM Settings) may not be configured yet, so a
    # build request here would crash the install on get_connection().
    if (
        frappe.flags.in_install
        or frappe.flags.in_migrate
        or frappe.flags.in_patch
        or frappe.flags.in_import
    ):
        return

    mapping = DOCTYPE_BUILD_MAP.get(doc.doctype)
    if not mapping:
        frappe.logger().warning(f"No build mapping for doctype: {doc.doctype}")
        return

    scope = mapping["scope"]

    # Debounce: skip if a non-terminal PBR already exists for this scope
    existing = frappe.get_all(
        "Pipeline Build Request",
        filters={
            "build_scope": scope,
            "workflow_state": ["in", ["Draft", "Pending Review", "Approved", "Running"]],
        },
        limit=1,
    )
    if existing:
        frappe.logger().info(
            f"Build request already pending for scope={scope} ({existing[0].name}), skipping"
        )
        return

    # Create Pipeline Build Request
    pbr = frappe.new_doc("Pipeline Build Request")
    pbr.build_scope = scope
    pbr.trigger_source = "auto"
    pbr.trigger_doctype = doc.doctype
    pbr.trigger_docname = doc.name
    pbr.requested_by = frappe.session.user
    pbr.insert(ignore_permissions=True)
    frappe.db.commit()

    frappe.logger().info(
        f"Pipeline Build Request {pbr.name} created (scope={scope}, trigger={doc.doctype} {doc.name})"
    )


# ---------------------------------------------------------------------------
# Legacy: Lightweight dbt-only build (kept for backward compat)
# ---------------------------------------------------------------------------
def run_dbt_build_async(doctype=None, docname=None):
    """Run dbt build as a background job. Debounced: skips if one is already queued.

    DEPRECATED: Use on_consolidation_doc_update → Pipeline Build Request instead.
    """
    from frappe.utils.background_jobs import get_jobs
    site = frappe.local.site
    queued_jobs = get_jobs(site=site, queue="default")
    for job_list in queued_jobs.values():
        if "konsol.tasks._run_dbt_build_background" in job_list:
            frappe.logger().info("dbt build already queued, skipping duplicate")
            return

    frappe.enqueue(
        "konsol.tasks._run_dbt_build_background",
        queue="default",
        timeout=600,
        doctype=doctype,
        docname=docname,
    )
    frappe.logger().info(
        f"dbt build enqueued (triggered by {doctype} {docname})"
    )


def _run_dbt_build_background(doctype=None, docname=None, pipeline_run=None):
    """Background worker: execute dbt build and log result.

    If pipeline_run is provided, updates its status on completion/failure.
    """
    settings = frappe.get_single("EPM Settings")
    project_path = settings.dbt_project_path

    try:
        result = subprocess.run(
            [_dbt_bin(), "build", "--project-dir", project_path, "--profiles-dir", project_path],
            capture_output=True,
            text=True,
            timeout=300,
            cwd=project_path,
        )
        output = result.stdout + "\n" + result.stderr

        if result.returncode == 0:
            summary = ""
            for line in output.split("\n"):
                if "pass" in line.lower() and ("warn" in line.lower() or "error" in line.lower()):
                    summary = line.strip()
                    break
            frappe.logger().info(f"dbt build completed: {summary or 'success'}")
            frappe.publish_realtime(
                "dbt_build_complete",
                {"status": "success", "summary": summary, "trigger": f"{doctype} {docname}"},
            )
            _update_pipeline_run(pipeline_run, "Completed", dbt_result=summary,
                                 log=output, project_path=project_path)
        else:
            frappe.logger().error(f"dbt build failed (rc={result.returncode}):\n{output[-2000:]}")
            frappe.publish_realtime(
                "dbt_build_complete",
                {"status": "failed", "error": output[-500:]},
            )
            _update_pipeline_run(pipeline_run, "Failed", error_log=output[-2000:],
                                 log=output, project_path=project_path)
    except subprocess.TimeoutExpired:
        frappe.logger().error("dbt build timed out after 300s")
        _update_pipeline_run(pipeline_run, "Failed", error_log="dbt build timed out after 300s")
    except Exception as e:
        frappe.logger().error(f"dbt build error: {e}")
        _update_pipeline_run(pipeline_run, "Failed", error_log=str(e))


def _update_pipeline_run(pipeline_run, status, dbt_result=None, error_log=None,
                         log=None, project_path=None):
    """Update a Pipeline Run's status if name was provided.

    When project_path is given, also parses target/run_results.json into the
    `steps` child table (Press-style per-node state) and stores the full `log`.
    """
    if not pipeline_run:
        return
    try:
        doc = frappe.get_doc("Pipeline Run", pipeline_run)
        doc.status = status
        doc.completed_at = frappe.utils.now_datetime()
        if dbt_result:
            doc.dbt_result = dbt_result
        if error_log:
            doc.error_log = error_log
        if log is not None:
            doc.log = log[-20000:]
        if project_path:
            _populate_run_steps(doc, project_path)
        doc.save(ignore_permissions=True)
        frappe.db.commit()
        frappe.publish_realtime(
            "pipeline_run_update",
            {"run": doc.name, "done": True, "progress": doc.progress_pct},
            doctype="Pipeline Run", docname=doc.name,
        )
    except Exception:
        frappe.log_error("Failed to update Pipeline Run status", frappe.get_traceback())


def _populate_run_steps(doc, project_path):
    """Read dbt target/run_results.json into the Pipeline Run `steps` table."""
    import json
    import os

    rr_path = os.path.join(project_path, "target", "run_results.json")
    try:
        with open(rr_path) as fh:
            rr = json.load(fh)
    except Exception:
        return

    status_map = {"success": "Success", "error": "Failure", "fail": "Failure",
                  "pass": "Success", "skipped": "Skipped"}
    doc.set("steps", [])
    done = 0
    nodes = rr.get("results", [])
    for node in nodes:
        uid = node.get("unique_id", "")
        parts = uid.split(".")
        rtype = parts[0] if parts else ""
        name = parts[2] if len(parts) > 2 else uid
        rel = (node.get("relation_name") or "").lower()
        if rtype == "seed":
            stage = "Seed"
        elif rtype == "test":
            stage = "Test"
        elif "bronze" in rel:
            stage = "Bronze"
        elif "silver" in rel:
            stage = "Silver"
        elif "gold" in rel:
            stage = "Gold"
        elif "staging" in rel or "stg_" in name:
            stage = "Staging"
        else:
            stage = "Model"
        st = status_map.get((node.get("status") or "").lower(), "Pending")
        if st in ("Success", "Skipped", "Failure"):
            done += 1
        rows = (node.get("adapter_response") or {}).get("rows_affected") or 0
        doc.append("steps", {
            "stage": stage,
            "step": name,
            "status": st,
            "rows": rows,
            "duration": round(node.get("execution_time") or 0, 2),
            "output": (node.get("message") or "")[:2000],
        })
    doc.progress_pct = int(100 * done / len(nodes)) if nodes else 0


# ---------------------------------------------------------------------------
# Full pipeline: Airbyte extract + dbt build
# ---------------------------------------------------------------------------
def run_pipeline(pipeline_run):
    """Main entry point — called by frappe.enqueue from trigger_pipeline."""
    doc = frappe.get_doc("Pipeline Run", pipeline_run)

    try:
        # Step 1: Airbyte extract
        _update_status(doc, "Extracting")
        job_id, rows = _run_airbyte_sync(doc)
        doc.airbyte_job_id = job_id
        doc.rows_synced = rows

        # Step 2: dbt build
        _update_status(doc, "Transforming")
        dbt_result = _run_dbt_build(doc)
        doc.dbt_result = dbt_result

        # Done
        doc.status = "Completed"
        doc.completed_at = frappe.utils.now_datetime()
        doc.save(ignore_permissions=True)
        frappe.db.commit()

        frappe.publish_realtime(
            "pipeline_progress",
            {"name": doc.name, "status": "Completed", "dbt_result": dbt_result},
            doctype="Pipeline Run",
            docname=doc.name,
        )

    except Exception as e:
        doc.status = "Failed"
        doc.error_log = str(e)
        doc.completed_at = frappe.utils.now_datetime()
        doc.save(ignore_permissions=True)
        frappe.db.commit()

        frappe.publish_realtime(
            "pipeline_progress",
            {"name": doc.name, "status": "Failed", "error": str(e)},
            doctype="Pipeline Run",
            docname=doc.name,
        )


def _update_status(doc, status):
    """Update doc status and publish realtime event."""
    doc.status = status
    doc.save(ignore_permissions=True)
    frappe.db.commit()

    frappe.publish_realtime(
        "pipeline_progress",
        {"name": doc.name, "status": status},
        doctype="Pipeline Run",
        docname=doc.name,
    )


def _run_airbyte_sync(doc):
    """Authenticate to Airbyte API, trigger sync, poll until done."""
    settings = frappe.get_single("EPM Settings")

    api_url = settings.airbyte_api_url.rstrip("/")
    client_id = settings.airbyte_client_id
    client_secret = settings.get_password("airbyte_client_secret")
    connection_id = settings.airbyte_connection_id

    # Get OAuth token
    token_resp = requests.post(
        f"{api_url}/api/v1/applications/token",
        json={"client_id": client_id, "client_secret": client_secret},
        timeout=30,
    )
    token_resp.raise_for_status()
    token = token_resp.json()["access_token"]

    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    # Trigger sync job
    job_resp = requests.post(
        f"{api_url}/api/v1/jobs",
        json={"connectionId": connection_id, "jobType": "sync"},
        headers=headers,
        timeout=30,
    )
    job_resp.raise_for_status()
    job_id = str(job_resp.json()["jobId"])

    # Poll until complete
    rows_synced = 0
    for _ in range(120):  # max 60 minutes (30s intervals)
        time.sleep(30)
        status_resp = requests.get(
            f"{api_url}/api/v1/jobs/{job_id}",
            headers=headers,
            timeout=30,
        )
        status_resp.raise_for_status()
        job_data = status_resp.json()
        job_status = job_data.get("status", "")

        if job_status == "succeeded":
            rows_synced = job_data.get("rowsSynced", 0)
            break
        elif job_status in ("failed", "cancelled"):
            raise Exception(f"Airbyte sync {job_status}: {job_data}")

    return job_id, rows_synced


def _run_dbt_build(doc):
    """Run dbt build via subprocess, return summary string."""
    settings = frappe.get_single("EPM Settings")
    project_path = settings.dbt_project_path

    result = subprocess.run(
        [_dbt_bin(), "build", "--project-dir", project_path, "--profiles-dir", project_path],
        capture_output=True,
        text=True,
        timeout=300,
        cwd=project_path,
    )

    output = result.stdout + "\n" + result.stderr

    if result.returncode != 0:
        raise Exception(f"dbt build failed (rc={result.returncode}):\n{output[-2000:]}")

    # Parse summary line like "Completed successfully. 42 pass, 0 warn, 0 error"
    summary = ""
    for line in output.split("\n"):
        if "pass" in line.lower() and ("warn" in line.lower() or "error" in line.lower()):
            summary = line.strip()
            break

    return summary or output[-500:]

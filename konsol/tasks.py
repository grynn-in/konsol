"""Background jobs: governed dbt builds + Airbyte pipeline.

Build governance: doc saves create Pipeline Build Requests instead of
firing raw `dbt build`. Scopes map to dbt domain tags for selective builds.
"""
import subprocess
import time

import frappe
import requests


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

# Scope → dbt selector
SCOPE_SELECTOR = {
    "staging": "tag:domain:staging",
    "actuals": "tag:domain:actuals",
    "scenarios": "tag:domain:scenarios",
    "consolidation": "tag:domain:consolidation",
    "full": None,  # no selector = full build
}

# Scopes that require epm_raw data
RAW_DEPENDENT_SCOPES = {"actuals", "scenarios", "consolidation", "full"}


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
    if build_scope in RAW_DEPENDENT_SCOPES:
        return check_raw_data_available()

    return True, "OK"


def check_raw_data_available():
    """Check if epm_raw has valid data via EPM Settings sync status.

    Returns (ok: bool, message: str).
    """
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
        cmd = ["dbt", "build", "--project-dir", project_path, "--profiles-dir", project_path]

        selector = SCOPE_SELECTOR.get(doc.build_scope)
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
            ["dbt", "build", "--project-dir", project_path, "--profiles-dir", project_path],
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
            _update_pipeline_run(pipeline_run, "Completed", dbt_result=summary)
        else:
            frappe.logger().error(f"dbt build failed (rc={result.returncode}):\n{output[-2000:]}")
            frappe.publish_realtime(
                "dbt_build_complete",
                {"status": "failed", "error": output[-500:]},
            )
            _update_pipeline_run(pipeline_run, "Failed", error_log=output[-2000:])
    except subprocess.TimeoutExpired:
        frappe.logger().error("dbt build timed out after 300s")
        _update_pipeline_run(pipeline_run, "Failed", error_log="dbt build timed out after 300s")
    except Exception as e:
        frappe.logger().error(f"dbt build error: {e}")
        _update_pipeline_run(pipeline_run, "Failed", error_log=str(e))


def _update_pipeline_run(pipeline_run, status, dbt_result=None, error_log=None):
    """Update a Pipeline Run's status if name was provided."""
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
        doc.save(ignore_permissions=True)
        frappe.db.commit()
    except Exception:
        frappe.log_error("Failed to update Pipeline Run status", frappe.get_traceback())


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
        ["dbt", "build", "--project-dir", project_path, "--profiles-dir", project_path],
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

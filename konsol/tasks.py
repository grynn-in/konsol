"""Background job: Airbyte extract + dbt build pipeline."""
import subprocess
import time

import frappe
import requests


# ---------------------------------------------------------------------------
# Lightweight dbt-only build (auto-triggered after doc saves)
# ---------------------------------------------------------------------------
def run_dbt_build_async(doctype=None, docname=None):
    """Run dbt build as a background job. Debounced: skips if one is already queued.

    Called automatically after consolidation/allocation doc saves via hooks.py.
    Unlike run_pipeline(), this skips Airbyte sync and just runs dbt build.
    """
    # Debounce: check if a dbt build is already queued or running
    from frappe.utils.background_jobs import get_jobs
    site = frappe.local.site
    queued_jobs = get_jobs(site=site, queue="default")
    for job_list in queued_jobs.values():
        if "konsol.tasks.run_dbt_build_async" in job_list:
            frappe.logger().info("dbt build already queued, skipping duplicate")
            return

    frappe.enqueue(
        "_run_dbt_build_background",
        queue="default",
        timeout=600,
        is_async=True,
        doctype=doctype,
        docname=docname,
    )
    frappe.logger().info(
        f"dbt build enqueued (triggered by {doctype} {docname})"
    )


def _run_dbt_build_background(doctype=None, docname=None):
    """Background worker: execute dbt build and log result."""
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
            # Parse summary
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
        else:
            frappe.logger().error(f"dbt build failed (rc={result.returncode}):\n{output[-2000:]}")
            frappe.publish_realtime(
                "dbt_build_complete",
                {"status": "failed", "error": output[-500:]},
            )
    except subprocess.TimeoutExpired:
        frappe.logger().error("dbt build timed out after 300s")
    except Exception as e:
        frappe.logger().error(f"dbt build error: {e}")


# ---------------------------------------------------------------------------
# Hook: trigger dbt build after consolidation/allocation doc changes
# ---------------------------------------------------------------------------
def on_consolidation_doc_update(doc, method):
    """Called by doc_events hook for consolidation/allocation doctypes.
    Enqueues a debounced dbt build after ClickHouse sync completes."""
    run_dbt_build_async(doctype=doc.doctype, docname=doc.name)


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

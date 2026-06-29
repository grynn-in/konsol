import frappe
from frappe.model.document import Document


class PipelineRun(Document):
    pass


@frappe.whitelist()
def trigger_pipeline():
    """Create a new Pipeline Run and enqueue the background job."""
    # #64a single-flight: this legacy path also shells dbt against the one shared
    # project dir, so it must honor the same guard as orchestrator.start_run —
    # otherwise two concurrent dbt builds can corrupt target/ and race incrementals.
    # #67 fix 4: it's a state-mutating entrypoint, so it needs the same EPM Admin
    # role guard the orchestrator API uses.
    from konsol.orchestrator.api import _assert_no_active_run, single_flight_lock
    from konsol.schema_lifecycle import check_epm_admin

    check_epm_admin()
    # #67 fix 1: take the single-flight lock across the check+insert so this path
    # and orchestrator.start_run serialise against each other.
    with single_flight_lock():
        _assert_no_active_run()
        doc = frappe.get_doc(
            {
                "doctype": "Pipeline Run",
                "status": "Queued",
                "triggered_by": frappe.session.user,
                "started_at": frappe.utils.now_datetime(),
            }
        )
        doc.insert(ignore_permissions=True)
        frappe.db.commit()

    frappe.enqueue(
        "konsol.tasks.run_pipeline",
        queue="default",
        timeout=600,
        pipeline_run=doc.name,
    )

    return doc.name

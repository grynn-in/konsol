import frappe
from frappe.model.document import Document


class PipelineRun(Document):
    pass


@frappe.whitelist()
def trigger_pipeline():
    """Create a new Pipeline Run and enqueue the background job."""
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

"""Schema Lifecycle — shared publish/unpublish helpers for Dimension and Measure.

Extracted to avoid code duplication between the two doctypes.
"""
import frappe

_ALLOWED_ROLES = {"EPM Admin", "System Manager", "Administrator"}


def check_epm_admin():
    """Guard: require EPM Admin, System Manager, or Administrator role."""
    if not _ALLOWED_ROLES.intersection(set(frappe.get_roles())):
        frappe.throw(
            "You need the 'EPM Admin' role to publish or unpublish.",
            frappe.PermissionError,
        )


def apply_and_rebuild(doc, action):
    """Run apply_schema then create a Pipeline Run for dbt rebuild."""
    from konsol.schema_apply import apply_schema
    apply_schema()
    _create_pipeline_run(doc, action)


def _create_pipeline_run(doc, action):
    """Create a Pipeline Run and enqueue a debounced dbt build."""
    run = frappe.get_doc({
        "doctype": "Pipeline Run",
        "status": "Queued",
        "triggered_by": frappe.session.user,
        "started_at": frappe.utils.now_datetime(),
    })
    run.insert(ignore_permissions=True)

    from konsol.tasks import run_dbt_build_async
    run_dbt_build_async(doctype=doc.doctype, docname=doc.name)

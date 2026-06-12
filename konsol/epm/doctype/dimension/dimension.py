"""Dimension — config doctype for EPM dimensions.

Saves are pure metadata — no side effects. Use Publish/Unpublish to apply
schema changes (DDL, dbt vars, budget fields) and trigger a Pipeline Run.
"""
import frappe
from frappe.model.document import Document


class Dimension(Document):

    @frappe.whitelist()
    def publish(self):
        """Publish this dimension: apply schema + trigger dbt rebuild."""
        _check_epm_admin()
        self.status = "Published"
        self.save()
        _apply_and_rebuild(self, "Publish")

    @frappe.whitelist()
    def unpublish(self):
        """Unpublish (deactivate) this dimension: apply schema + trigger dbt rebuild."""
        _check_epm_admin()
        self.status = "Inactive"
        self.save()
        _apply_and_rebuild(self, "Unpublish")


def _check_epm_admin():
    """Guard: require EPM Admin or System Manager role."""
    roles = frappe.get_roles()
    if "System Manager" not in roles and "EPM Admin" not in roles:
        frappe.throw(
            "You need the 'EPM Admin' role to publish or unpublish.",
            frappe.PermissionError,
        )


def _apply_and_rebuild(doc, action):
    """Run apply_schema then create a Pipeline Run for dbt rebuild."""
    from konsol.schema_apply import apply_schema
    apply_schema()

    _create_pipeline_run(doc, action)


def _create_pipeline_run(doc, action):
    """Create a Pipeline Run and enqueue dbt build."""
    run = frappe.get_doc({
        "doctype": "Pipeline Run",
        "status": "Queued",
        "triggered_by": frappe.session.user,
        "started_at": frappe.utils.now_datetime(),
    })
    run.insert(ignore_permissions=True)
    frappe.db.commit()

    frappe.enqueue(
        "konsol.tasks._run_dbt_build_background",
        queue="default",
        timeout=600,
        doctype=doc.doctype,
        docname=doc.name,
    )

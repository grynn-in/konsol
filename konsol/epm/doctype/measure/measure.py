"""Measure — config doctype for EPM measures.

Saves are pure metadata — no side effects. Use Publish/Unpublish to apply
schema changes (dbt vars) and request a governed full-scope rebuild via
Build Approval (preflight + approval + audit), not a direct dbt build.
"""
import frappe
from frappe.model.document import Document

from konsol.schema_lifecycle import apply_and_rebuild, check_epm_admin


class Measure(Document):

    @frappe.whitelist()
    def publish(self):
        """Publish this measure: apply schema + trigger dbt rebuild."""
        check_epm_admin()
        self.status = "Published"
        self.save()
        apply_and_rebuild(self, "Publish")

    @frappe.whitelist()
    def unpublish(self):
        """Unpublish (deactivate) this measure: apply schema + trigger dbt rebuild."""
        check_epm_admin()
        self.status = "Inactive"
        self.save()
        apply_and_rebuild(self, "Unpublish")

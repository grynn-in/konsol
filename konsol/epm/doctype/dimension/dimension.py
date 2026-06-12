"""Dimension — config doctype for EPM dimensions.

Saves are pure metadata — no side effects. Use Publish/Unpublish to apply
schema changes (DDL, dbt vars, budget fields) and trigger a Pipeline Run.
"""
import frappe
from frappe.model.document import Document

from konsol.schema_lifecycle import apply_and_rebuild, check_epm_admin


class Dimension(Document):

    @frappe.whitelist()
    def publish(self):
        """Publish this dimension: apply schema + trigger dbt rebuild."""
        check_epm_admin()
        self.status = "Published"
        self.save()
        apply_and_rebuild(self, "Publish")

    @frappe.whitelist()
    def unpublish(self):
        """Unpublish (deactivate) this dimension: apply schema + trigger dbt rebuild."""
        check_epm_admin()
        self.status = "Inactive"
        self.save()
        apply_and_rebuild(self, "Unpublish")

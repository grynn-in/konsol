"""Reporting Hierarchy — management reporting trees on canonical dimensions.

Legal-entity consolidation uses Consolidation Group, not this doctype.
Publish regenerates seeds/reporting_hierarchies.csv and requests a governed
reporting-scope build.
"""
import frappe
from frappe.model.document import Document

from konsol.dbt_config import regenerate_reporting_hierarchies_seed
from konsol.schema_lifecycle import check_epm_admin, request_governed_rebuild

_REPORTING_BUILD_SCOPE = "reporting"


class ReportingHierarchy(Document):

    def validate(self):
        self._validate_default_unique()
        if self.status == "Published":
            self._validate_dimension_published()

    def _validate_dimension_published(self):
        if not self.dimension:
            return
        status = frappe.db.get_value("Dimension", self.dimension, "status")
        if status != "Published":
            frappe.throw(
                f"Dimension '{self.dimension}' must be Published before saving "
                f"a Reporting Hierarchy. Publish the dimension first."
            )

    def _validate_default_unique(self):
        if not self.is_default:
            return
        dupe = frappe.db.exists(
            "Reporting Hierarchy",
            {
                "dimension": self.dimension,
                "is_default": 1,
                "status": ["!=", "Inactive"],
                "name": ["!=", self.name],
            },
        )
        if dupe:
            frappe.throw(
                f"Dimension '{self.dimension}' already has a default hierarchy "
                f"({dupe}). Clear is_default on the other hierarchy first."
            )

    def _validate_publish_ready(self):
        if frappe.db.count(
            "Reporting Hierarchy Member",
            {"reporting_hierarchy": self.name},
        ) == 0:
            frappe.throw(
                "Add at least one Reporting Hierarchy Member before publishing."
            )

    @frappe.whitelist()
    def publish(self):
        """Publish hierarchy + members → regenerate seed → reporting PBR."""
        check_epm_admin()
        self._validate_dimension_published()
        self._validate_publish_ready()
        self.status = "Published"
        self.save()
        regenerate_reporting_hierarchies_seed()
        request_governed_rebuild(self, "Publish", scope=_REPORTING_BUILD_SCOPE)

    @frappe.whitelist()
    def unpublish(self):
        """Mark inactive, refresh seed, request reporting rebuild."""
        check_epm_admin()
        self.status = "Inactive"
        self.save()
        regenerate_reporting_hierarchies_seed()
        request_governed_rebuild(self, "Unpublish", scope=_REPORTING_BUILD_SCOPE)

    def on_trash(self):
        if self.status == "Published":
            frappe.throw(
                "Unpublish this Reporting Hierarchy before deleting it."
            )
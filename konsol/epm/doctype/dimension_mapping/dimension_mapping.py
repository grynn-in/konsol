"""Dimension Mapping — crosswalk from a raw ERP dimension value to a canonical one.

Saves are pure metadata. Use Publish/Unpublish to (re)generate the
seeds/dimension_mappings.csv crosswalk consumed by the dbt dim_harmonize()
macro and request a governed rebuild. Keyed on (dimension, erp_source,
source_value), which must be unique among non-Inactive rows.
"""
import frappe
from frappe.model.document import Document

from konsol.dbt_config import regenerate_dimension_mappings_seed
from konsol.schema_lifecycle import check_epm_admin, request_governed_rebuild


class DimensionMapping(Document):

    def validate(self):
        self._validate_unique_key()

    def _validate_unique_key(self):
        """(dimension, erp_source, source_value) must map to one canonical value.

        Enforced against other non-Inactive rows so a source value never has two
        live crosswalk targets for the same ERP.
        """
        dupe = frappe.db.exists(
            "Dimension Mapping",
            {
                "dimension": self.dimension,
                "erp_source": self.erp_source,
                "source_value": self.source_value,
                "status": ["!=", "Inactive"],
                "name": ["!=", self.name],
            },
        )
        if dupe:
            frappe.throw(
                f"A mapping for {self.dimension} / {self.erp_source} / "
                f"'{self.source_value}' already exists ({dupe})."
            )

    @frappe.whitelist()
    def publish(self):
        """Publish: regenerate the crosswalk seed + request a governed rebuild."""
        check_epm_admin()
        self.status = "Published"
        self.save()
        regenerate_dimension_mappings_seed()
        request_governed_rebuild(self, "Publish")

    @frappe.whitelist()
    def unpublish(self):
        """Unpublish (Inactive): regenerate seed + request a governed rebuild."""
        check_epm_admin()
        self.status = "Inactive"
        self.save()
        regenerate_dimension_mappings_seed()
        request_governed_rebuild(self, "Unpublish")

    def after_delete(self):
        """Refresh the crosswalk seed after a *Published* mapping is removed, so
        the deleted value stops being applied.

        Uses after_delete (not on_trash): on_trash runs *before* the row is
        removed, so regenerating there would still include the doc being deleted.
        Skipped during install/migrate/import — the seed is regenerated wholesale
        by after_migrate then, and no build should be enqueued mid-migrate.
        """
        if frappe.flags.in_install or frappe.flags.in_migrate or frappe.flags.in_patch:
            return
        if self.status == "Published":
            regenerate_dimension_mappings_seed()
            request_governed_rebuild(self, "Delete")

"""Dimension Mapping — crosswalk from a raw ERP dimension value to a canonical one.

Saves are pure metadata. Use Publish/Unpublish to (re)generate the
seeds/dimension_mappings.csv crosswalk consumed by the dbt dim_harmonize()
macro and request a governed rebuild. Keyed on (dimension, erp_source,
source_value), which must be unique among non-Inactive rows.
"""
import frappe
from frappe.model.document import Document

from konsol.clickhouse import sync_doctype_filtered
from konsol.schema_lifecycle import check_epm_admin, request_governed_rebuild


class DimensionMapping(Document):
    # F3: write-through to the warehouse — the crosswalk USED to be written as
    # a CSV seed into the dbt repo on every save and migrate. Only Published
    # rows sync (dbt filtered on status='Published' already); TRUNCATE+INSERT,
    # reconciled after migrate like every other write-through table.
    CH_TABLE = "epm_staging.dimension_mappings"
    CH_FIELD_MAP = {
        "dimension": "dimension",
        "erp_source": "erp_source",
        "source_value": "source_value",
        "canonical_value": "canonical_value",
        "canonical_label": "canonical_label",
        "status": "status",
    }


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
        sync_doctype_filtered(
            "Dimension Mapping", self.CH_TABLE, self.CH_FIELD_MAP,
            filters={"status": "Published"},
        )
        request_governed_rebuild(self, "Publish")

    @frappe.whitelist()
    def unpublish(self):
        """Unpublish (Inactive): regenerate seed + request a governed rebuild."""
        check_epm_admin()
        self.status = "Inactive"
        self.save()
        sync_doctype_filtered(
            "Dimension Mapping", self.CH_TABLE, self.CH_FIELD_MAP,
            filters={"status": "Published"},
        )
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
            sync_doctype_filtered(
            "Dimension Mapping", self.CH_TABLE, self.CH_FIELD_MAP,
            filters={"status": "Published"},
        )
            request_governed_rebuild(self, "Delete")

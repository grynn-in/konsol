"""Reporting Hierarchy — management reporting trees on canonical dimensions.

Legal-entity consolidation uses Consolidation Group, not this doctype.
Publish re-syncs epm_staging.reporting_hierarchies and requests a governed
reporting-scope build.
"""
import frappe

from konsol.clickhouse import sync_table
from konsol.governed_reference import GovernedReferenceDocument
from konsol.reporting_hierarchy_seed import flatten_reporting_hierarchies

_REPORTING_BUILD_SCOPE = "reporting"


class ReportingHierarchy(GovernedReferenceDocument):
    # F3: write-through replaces the CSV seed. The rows are COMPUTED (the
    # hierarchy is flattened member-by-member), so this uses the
    # resync_staging() pattern Consolidation Group established — reconcile_all
    # calls it without needing an instance, and an empty doctype still
    # truncates the table. flatten_reporting_hierarchies() already reads
    # Published headers only, which is the filter the base class expresses as
    # CH_SYNC_FILTERS for the field-mapped doctypes.
    CH_STAGING_TABLE = "epm_staging.reporting_hierarchies"
    CH_STAGING_COLUMNS = [
        "hierarchy_name", "dimension", "member_code", "member_label",
        "parent_member_code", "is_group", "hierarchy_level", "path",
        "effective_from", "effective_to", "is_default", "status",
    ]
    BUILD_SCOPE = _REPORTING_BUILD_SCOPE

    @classmethod
    def resync_staging(cls, force=False):
        """Flatten every Published hierarchy into the staging table.

        Returns what ``sync_table`` actually wrote — None when the write failed
        or was skipped. It used to return ``len(data)`` unconditionally, so a
        reconcile whose ClickHouse write was refused still logged a row count
        for this table and the migrate read as a clean repair, with no
        watermark behind it.
        """
        rows = flatten_reporting_hierarchies(frappe)
        data = [[r.get(c) if r.get(c) is not None else "" for c in cls.CH_STAGING_COLUMNS]
                for r in rows]
        return sync_table(cls.CH_STAGING_TABLE, cls.CH_STAGING_COLUMNS, data, force=force)

    def _resync(self):
        """The rows are computed, not field-mapped — flatten instead."""
        return type(self).resync_staging()

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

    def _before_publish(self):
        """Publish hierarchy + members → re-sync staging → reporting PBR."""
        self._validate_dimension_published()
        self._validate_publish_ready()

    def on_trash(self):
        if self.status == "Published":
            frappe.throw(
                "Unpublish this Reporting Hierarchy before deleting it."
            )

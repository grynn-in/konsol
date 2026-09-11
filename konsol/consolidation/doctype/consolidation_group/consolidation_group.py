"""Consolidation Group — entity groupings for financial consolidation.

PRD-8: Multi-level hierarchy via Frappe's native tree (is_tree=1, lft/rgt)
PRD-13: Goodwill method (partial/full)
PRD-14: Equity method support (consolidation_method = 'equity')
"""
import frappe
from frappe.utils.nestedset import NestedSet

from konsol.clickhouse import sync_doctype, sync_table


class ConsolidationGroup(NestedSet):
    # Legacy sync to gold.* (seed replacement)
    CH_TABLE = "epm_gold.consolidation_groups"
    CH_FIELD_MAP = {
        "consolidation_group": "consolidation_group",
        "data_area_id": "data_area_id",
        "entity_name": "entity_name",
        "ownership_pct": "ownership_pct",
        "reporting_currency": "reporting_currency",
        "consolidation_method": "consolidation_method",
    }

    # PRD-8: Staging sync for hierarchy data
    CH_STAGING_TABLE = "epm_staging.consolidation_hierarchy"
    CH_STAGING_COLUMNS = [
        "consolidation_group", "data_area_id", "parent_group",
        "hierarchy_level", "effective_ownership_pct", "path",
    ]

    def on_update(self):
        super().on_update()
        sync_doctype(self.doctype, self.CH_TABLE, self.CH_FIELD_MAP)
        self._sync_hierarchy()

    def on_trash(self):
        super().on_trash()
        sync_doctype(self.doctype, self.CH_TABLE, self.CH_FIELD_MAP)
        self._sync_hierarchy()

    @classmethod
    def resync_staging(cls, force=False):
        """Rebuild epm_staging.consolidation_hierarchy from every group doc.

        The hierarchy rows are COMPUTED (tree walk), not field-mapped, so the
        generic reconcile cannot produce them; this is the hook it calls. The
        instance method below now delegates here — the sync always operated on
        all documents regardless of which one triggered it.
        """
        docs = frappe.get_all(
            cls.CH_STAGING_DOCTYPE if hasattr(cls, "CH_STAGING_DOCTYPE") else "Consolidation Group",
            fields=[
                "name", "consolidation_group", "data_area_id",
                "parent_consolidation_group", "lft", "rgt", "ownership_pct",
            ],
            limit_page_length=0,
        )
        by_name = {d.name: d for d in docs}
        rows = []
        for d in docs:
            ancestors = cls._get_ancestors(d, by_name)
            level = len(ancestors) + 1
            path_parts = [by_name[a].consolidation_group for a in reversed(ancestors)]
            path_parts.append(d.consolidation_group)
            if d.data_area_id:
                path_parts.append(d.data_area_id)
            rows.append([
                d.consolidation_group,
                d.data_area_id or "",
                by_name[d.parent_consolidation_group].consolidation_group
                if d.parent_consolidation_group and d.parent_consolidation_group in by_name
                else "",
                level,
                d.ownership_pct or 100,
                "/".join(path_parts),
            ])
        # Return what sync_table wrote — None when it failed or was skipped.
        # Returning nothing made reconcile_all record None for a *successful*
        # sync, which now reads as a failure; the same omission in
        # ReportingHierarchy.resync_staging hid real failures the other way.
        return sync_table(cls.CH_STAGING_TABLE, cls.CH_STAGING_COLUMNS, rows,
                          force=force)

    def _sync_hierarchy(self):
        """PRD-8: kept as the hook on_update/on_trash call; the work moved to
        resync_staging() so the reconcile can run it without an instance."""
        type(self).resync_staging()

    @staticmethod
    def _get_ancestors(doc, by_name):
        """Walk parent_consolidation_group chain to root. Returns list of ancestor names."""
        ancestors = []
        current = doc.parent_consolidation_group
        seen = set()
        while current and current in by_name and current not in seen:
            seen.add(current)
            ancestors.append(current)
            current = by_name[current].parent_consolidation_group
        return ancestors

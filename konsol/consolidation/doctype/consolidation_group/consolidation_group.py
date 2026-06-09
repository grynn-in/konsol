"""Consolidation Group — entity groupings for financial consolidation.

PRD-8: Multi-level hierarchy (parent_group, hierarchy_level)
PRD-13: Goodwill method (partial/full)
PRD-14: Equity method support (consolidation_method = 'equity')
"""
import frappe
from frappe.model.document import Document

from konsol.clickhouse import sync_doctype, sync_table


class ConsolidationGroup(Document):
    # Legacy sync to gold.* (seed replacement)
    CH_TABLE = "gold.consolidation_groups"
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
        sync_doctype(self.doctype, self.CH_TABLE, self.CH_FIELD_MAP)
        self._sync_hierarchy()

    def on_trash(self):
        sync_doctype(self.doctype, self.CH_TABLE, self.CH_FIELD_MAP)
        self._sync_hierarchy()

    def _sync_hierarchy(self):
        """PRD-8: Build flattened hierarchy and sync to epm_staging."""
        docs = frappe.get_all(
            self.doctype,
            fields=[
                "consolidation_group", "data_area_id", "parent_group",
                "hierarchy_level", "ownership_pct",
            ],
            limit_page_length=0,
        )
        rows = []
        for d in docs:
            path = self._build_path(d.consolidation_group, d.data_area_id, docs)
            rows.append([
                d.consolidation_group,
                d.data_area_id or "",
                d.parent_group or "",
                d.hierarchy_level or 1,
                d.ownership_pct or 100,
                path,
            ])
        sync_table(self.CH_STAGING_TABLE, self.CH_STAGING_COLUMNS, rows)

    @staticmethod
    def _build_path(group, entity, all_docs):
        """Build hierarchy path string (e.g. GROUP_CORP/GROUP_EMEA/GBMF)."""
        parts = []
        current = group
        seen = set()
        while current and current not in seen:
            seen.add(current)
            parts.insert(0, current)
            parent = None
            for d in all_docs:
                if d.consolidation_group == current and d.parent_group:
                    parent = d.parent_group
                    break
            current = parent
        if entity:
            parts.append(entity)
        return "/".join(parts)

"""Allocation Driver — driver values synced to ClickHouse.

PRD-17: Unified driver table (epm_staging.allocation_drivers) + legacy per-type tables.
"""
import frappe
from frappe.model.document import Document

from konsol.clickhouse import sync_table


LEGACY_DRIVER_TYPES = ["headcount", "revenue", "sqm"]
LEGACY_COLUMNS = ["data_area_id", "cost_center", "driver_value", "fiscal_year", "fiscal_period"]

STAGING_COLUMNS = ["driver_type", "data_area_id", "cost_center", "fiscal_year", "fiscal_period", "driver_value"]


class AllocationDriver(Document):
    def on_update(self):
        self._sync_legacy_tables()
        self._sync_staging_table()

    def on_trash(self):
        self._sync_legacy_tables()
        self._sync_staging_table()

    def _sync_legacy_tables(self):
        """Legacy: sync each standard driver_type to gold.allocation_drivers_{type}."""
        for dtype in LEGACY_DRIVER_TYPES:
            table = f"gold.allocation_drivers_{dtype}"
            docs = frappe.get_all(
                self.doctype,
                filters={"driver_type": dtype},
                fields=LEGACY_COLUMNS,
                limit_page_length=0,
            )
            rows = [[d.get(c) for c in LEGACY_COLUMNS] for d in docs]
            sync_table(table, LEGACY_COLUMNS, rows)

    def _sync_staging_table(self):
        """PRD-17: Sync ALL driver types to unified epm_staging.allocation_drivers."""
        docs = frappe.get_all(
            self.doctype,
            fields=STAGING_COLUMNS,
            limit_page_length=0,
        )
        rows = [[d.get(c) for c in STAGING_COLUMNS] for d in docs]
        sync_table("epm_staging.allocation_drivers", STAGING_COLUMNS, rows)

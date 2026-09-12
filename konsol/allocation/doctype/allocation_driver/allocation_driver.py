"""Allocation Driver — driver values synced to ClickHouse.

PRD-17: Unified driver table (epm_staging.allocation_drivers).

konsolidat#146: the legacy per-type tables are gone. They wrote
`gold.allocation_drivers_{type}` — a database that is not even `epm_gold`, so
nothing has read them in a long time — while allocation/bootstrap.py wrote the
`epm_gold` spelling of the same three names, which were the deleted seeds'
relations. Every dbt reader uses the unified staging table, which carries all
types in one table with a driver_type column.
"""
import frappe
from frappe.model.document import Document

from konsol.clickhouse import sync_table



STAGING_COLUMNS = ["driver_type", "data_area_id", "cost_center", "fiscal_year", "fiscal_period", "driver_value"]


class AllocationDriver(Document):
    def on_update(self):
        self._sync_staging_table()

    def on_trash(self):
        self._sync_staging_table()

    def _sync_staging_table(self):
        """PRD-17: Sync ALL driver types to unified epm_staging.allocation_drivers."""
        docs = frappe.get_all(
            self.doctype,
            fields=STAGING_COLUMNS,
            limit_page_length=0,
        )
        rows = [[d.get(c) for c in STAGING_COLUMNS] for d in docs]
        sync_table("epm_staging.allocation_drivers", STAGING_COLUMNS, rows)

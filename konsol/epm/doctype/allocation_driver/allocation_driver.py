"""Allocation Driver — driver values (headcount/revenue/sqm) synced to ClickHouse.

Each driver_type syncs to its own CH table: gold.allocation_drivers_{type}.
"""
import frappe
from frappe.model.document import Document

from konsol.clickhouse import sync_table, get_connection


DRIVER_TYPES = ["headcount", "revenue", "sqm"]

CH_COLUMNS = ["data_area_id", "cost_center", "driver_value", "fiscal_year", "fiscal_period"]


class AllocationDriver(Document):
    def on_update(self):
        self._sync_all_driver_tables()

    def on_trash(self):
        self._sync_all_driver_tables()

    def _sync_all_driver_tables(self):
        """Sync each driver_type to its own CH table."""
        for dtype in DRIVER_TYPES:
            table = f"gold.allocation_drivers_{dtype}"
            docs = frappe.get_all(
                self.doctype,
                filters={"driver_type": dtype},
                fields=CH_COLUMNS,
                limit_page_length=0,
            )
            rows = [[d.get(c) for c in CH_COLUMNS] for d in docs]
            sync_table(table, CH_COLUMNS, rows)

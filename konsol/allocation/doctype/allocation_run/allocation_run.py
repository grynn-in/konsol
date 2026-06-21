"""Allocation Run — run metadata for traceability and reversibility.

PRD-21: Tracks allocation executions (Active/Reversed) with audit trail.
"""
import frappe
from frappe.model.document import Document
from frappe.utils import now_datetime

RUN_CH_TABLE = "epm_staging.allocation_runs"
RUN_CH_FIELD_MAP = {
    "allocation_run_id": "allocation_run_id",
    "fiscal_year": "fiscal_year",
    "fiscal_period": "fiscal_period",
    "status": "status",
    "run_by": "run_by",
    "run_at": "run_at",
    "reversal_of": "reversal_of",
}


def _format_run_cell(frappe_field, value, doc_name):
    """Normalize Frappe values for ClickHouse INSERT."""
    if frappe_field == "allocation_run_id":
        return value or doc_name
    if frappe_field == "run_at" and value is not None:
        return str(value)[:19]
    return value


def sync_allocation_runs_to_clickhouse():
    """Sync submitted/cancelled allocation runs to epm_staging.allocation_runs."""
    from konsol.clickhouse import sync_table

    if not frappe.db.table_exists("Allocation Run"):
        return

    ch_columns = list(RUN_CH_FIELD_MAP.keys())
    frappe_fields = list(RUN_CH_FIELD_MAP.values())
    docs = frappe.get_all(
        "Allocation Run",
        filters={"docstatus": ["in", [1, 2]]},
        fields=["name", *frappe_fields],
        limit_page_length=0,
    )
    rows = [
        [
            _format_run_cell(frappe_field, doc.get(frappe_field), doc.name)
            for frappe_field in frappe_fields
        ]
        for doc in docs
    ]
    sync_table(RUN_CH_TABLE, ch_columns, rows)


class AllocationRun(Document):
    CH_TABLE = RUN_CH_TABLE
    CH_FIELD_MAP = RUN_CH_FIELD_MAP

    def before_submit(self):
        self.allocation_run_id = self.name
        self.run_by = frappe.session.user
        self.run_at = now_datetime()
        self.status = "Active"

    def on_submit(self):
        frappe.db.after_commit.add(sync_allocation_runs_to_clickhouse)

    def on_cancel(self):
        self.status = "Reversed"
        self.save()
        frappe.db.after_commit.add(sync_allocation_runs_to_clickhouse)

    def on_trash(self):
        frappe.db.after_commit.add(sync_allocation_runs_to_clickhouse)
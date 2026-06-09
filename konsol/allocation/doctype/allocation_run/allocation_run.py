"""Allocation Run — run metadata for traceability and reversibility.

PRD-21: Tracks allocation executions (Active/Reversed) with audit trail.
"""
import frappe
from frappe.model.document import Document
from frappe.utils import now_datetime

from konsol.clickhouse import sync_doctype


class AllocationRun(Document):
    CH_TABLE = "epm_staging.allocation_runs"
    CH_FIELD_MAP = {
        "allocation_run_id": "allocation_run_id",
        "fiscal_year": "fiscal_year",
        "fiscal_period": "fiscal_period",
        "status": "status",
        "run_by": "run_by",
        "run_at": "run_at",
        "reversal_of": "reversal_of",
    }

    def before_submit(self):
        self.allocation_run_id = self.name
        self.run_by = frappe.session.user
        self.run_at = now_datetime()
        self.status = "Active"

    def on_submit(self):
        sync_doctype(self.doctype, self.CH_TABLE, self.CH_FIELD_MAP)

    def on_cancel(self):
        self.status = "Reversed"
        self.save()
        sync_doctype(self.doctype, self.CH_TABLE, self.CH_FIELD_MAP)

    def on_trash(self):
        sync_doctype(self.doctype, self.CH_TABLE, self.CH_FIELD_MAP)

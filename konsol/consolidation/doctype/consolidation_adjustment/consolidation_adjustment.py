"""Consolidation Adjustment — topside journals with workflow.

PRD-16: Status workflow (Draft → Pending Approval → Approved → Reversed),
        auto-reversal, approval tracking.
"""
import frappe
from frappe.model.document import Document
from frappe.utils import now_datetime

from konsol.clickhouse import sync_doctype


class ConsolidationAdjustment(Document):
    # Legacy sync (seed replacement)
    CH_TABLE = "epm_gold.consolidation_adjustments"
    CH_LEGACY_FIELD_MAP = {
        "consolidation_group": "consolidation_group",
        "adjustment_type": "adjustment_type",
        "journal_id": "journal_id",
        "data_area_id": "data_area_id",
        "fiscal_year": "fiscal_year",
        "fiscal_period": "fiscal_period",
        "main_account": "main_account",
        "debit_amount": "debit_amount",
        "credit_amount": "credit_amount",
        "description": "description",
        "posted_by": "posted_by",
    }

    # PRD-16: Staging sync with workflow fields
    CH_STAGING_TABLE = "epm_staging.consolidation_adjustments"
    CH_STAGING_FIELD_MAP = {
        "consolidation_group": "consolidation_group",
        "adjustment_type": "adjustment_type",
        "journal_id": "journal_id",
        "data_area_id": "data_area_id",
        "fiscal_year": "fiscal_year",
        "fiscal_period": "fiscal_period",
        "main_account": "main_account",
        "debit_amount": "debit_amount",
        "credit_amount": "credit_amount",
        "description": "description",
        "posted_by": "posted_by",
        "status": "status",
        "approved_by": "approved_by",
        "approved_at": "approved_at",
        "reversal_journal_id": "reversal_journal_id",
        "auto_reverse_period": "auto_reverse_period",
    }

    def validate(self):
        if self.status == "Approved" and not self.approved_by:
            self.approved_by = frappe.session.user
            self.approved_at = now_datetime()

    def on_update(self):
        sync_doctype(self.doctype, self.CH_TABLE, self.CH_LEGACY_FIELD_MAP)
        sync_doctype(self.doctype, self.CH_STAGING_TABLE, self.CH_STAGING_FIELD_MAP)

    def on_trash(self):
        sync_doctype(self.doctype, self.CH_TABLE, self.CH_LEGACY_FIELD_MAP)
        sync_doctype(self.doctype, self.CH_STAGING_TABLE, self.CH_STAGING_FIELD_MAP)

    def on_submit(self):
        if self.status == "Draft":
            self.status = "Pending Approval"
            self.save()

    def on_cancel(self):
        self.status = "Reversed"
        self.save()

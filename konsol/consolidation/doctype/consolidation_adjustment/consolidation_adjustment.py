"""Consolidation Adjustment — topside journals with workflow.

PRD-16: Status workflow (Draft → Pending Approval → Approved → Reversed),
        auto-reversal, approval tracking.
"""
import frappe
from frappe.model.document import Document
from frappe.utils import now_datetime

from konsol.clickhouse import sync_doctype
from konsol.period_status import assert_open


class ConsolidationAdjustment(Document):
    # konsolidat#146: the legacy epm_gold write-through is GONE. It existed to
    # replace a dbt seed — and seeds materialise into epm_gold, so the CSV and
    # this sync were the SAME ClickHouse relation, overwriting each other on
    # every `dbt seed` and every `bench migrate`. The seed is deleted and every
    # dbt reader moved to the staging table below, which is the richer one
    # anyway (the legacy map dropped the workflow/method columns entirely).
    # The legacy map also had no `status` column, and the dbt model labelled
    # everything it read from that relation 'Approved' unconditionally — so the
    # workflow only ever held because the model preferred staging whenever it
    # was non-empty.

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

    def before_submit(self):
        """Submit IS the approval (decided 12 Sep 2026). Set the status here,
        before the row is written. The old on_submit called self.save() on the
        submitted doc, an update-after-submit on a field that isn't
        allow_on_submit, so every submit raised and rolled back (#131)."""
        self.status = "Approved"
        if not self.approved_by:
            self.approved_by = frappe.session.user
            self.approved_at = now_datetime()

    def before_cancel(self):
        """Reverse only while the period is open. After close, a correction
        is a NEW adjustment in an open period. Set here: a cancelled document
        can't be saved at all, so the old on_cancel's self.save() raised."""
        assert_open(self.fiscal_year, self.fiscal_period, action="reverse a consolidation adjustment")
        self.status = "Reversed"

    # The warehouse holds only submitted rows (resolve_sync_filters:
    # docstatus=1), so submit adds the row, cancel removes it, and a draft
    # save changes nothing it reads. Synced after the commit (konsol#124).
    def on_submit(self):
        _queue_sync()

    def on_cancel(self):
        _queue_sync()

    def after_delete(self):
        """after_delete, not on_trash: on_trash ran before the row was gone,
        so the sync re-published it (#120)."""
        _queue_sync()


def _queue_sync():
    queued = getattr(frappe.db.after_commit, "_functions", ())
    if _sync_adjustments not in queued:
        frappe.db.after_commit.add(_sync_adjustments)


def _sync_adjustments():
    sync_doctype("Consolidation Adjustment", ConsolidationAdjustment.CH_STAGING_TABLE,
                 ConsolidationAdjustment.CH_STAGING_FIELD_MAP)

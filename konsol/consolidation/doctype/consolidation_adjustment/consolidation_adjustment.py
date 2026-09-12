"""Consolidation Adjustment — topside journals with workflow.

PRD-16: Status workflow (Draft → Pending Approval → Approved → Reversed),
        auto-reversal, approval tracking.
"""
import frappe
from frappe import _
from frappe.model.document import Document
from frappe.model.workflow import get_workflow_name
from frappe.utils import cint, now_datetime

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

    def before_insert(self):
        """A new adjustment starts as a draft. An amendment copies the
        cancelled original's status and approver (copy_doc ignores no_copy
        when amending), and the workflow refuses a new doc in a later state."""
        if self.status and self.status not in _states(0):
            self.status = _first_state()
        self.approved_by = self.approved_at = None

    def validate(self):
        """Approved and Reversed are set only by the submit and the cancel. A
        draft saved straight into one (a REST PUT) would look approved, never
        reach the warehouse, and have no transition out."""
        if self.docstatus == 0 and self.status and self.status not in _states(0):
            frappe.throw(_("{0} is set by approving or reversing the adjustment, not by saving it.").format(self.status))

    def before_submit(self):
        """Submit IS the approval (decided 12 Sep 2026). The old on_submit
        called self.save() on the submitted doc, an update-after-submit on a
        field that isn't allow_on_submit, so every submit raised (#131)."""
        if get_workflow_name(self.doctype):
            # apply_workflow sets the submitted state before it submits. A
            # direct submit would land a review state at docstatus 1.
            if self.status not in _states(1):
                frappe.throw(_("Approve the adjustment through its workflow."))
        else:
            self.status = "Approved"
        self.approved_by = frappe.session.user
        self.approved_at = now_datetime()

    def before_cancel(self):
        """Reverse only while the period is open. After close, a correction
        is a NEW adjustment in an open period. A cancelled document can't be
        saved at all, so the old on_cancel's self.save() raised."""
        assert_open(self.fiscal_year, self.fiscal_period, action="reverse a consolidation adjustment")
        if get_workflow_name(self.doctype):
            if self.status not in _states(2):
                frappe.throw(_("Reverse the adjustment through its workflow."))
        else:
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


def _workflow():
    name = get_workflow_name("Consolidation Adjustment")
    return frappe.get_cached_doc("Workflow", name) if name else None


def _states(docstatus):
    """The status values valid at a docstatus: the active workflow's, which a
    site may rename, else the built-in ones."""
    wf = _workflow()
    if wf:
        return {s.state for s in wf.states if cint(s.doc_status) == docstatus}
    return {0: {"Draft", "Pending Approval"}, 1: {"Approved"}, 2: {"Reversed"}}[docstatus]


def _first_state():
    wf = _workflow()
    return wf.states[0].state if wf else "Draft"


def _queue_sync():
    queued = getattr(frappe.db.after_commit, "_functions", ())
    if _sync_adjustments not in queued:
        frappe.db.after_commit.add(_sync_adjustments)


def _sync_adjustments():
    sync_doctype("Consolidation Adjustment", ConsolidationAdjustment.CH_STAGING_TABLE,
                 ConsolidationAdjustment.CH_STAGING_FIELD_MAP)

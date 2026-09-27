"""Consolidation Adjustment — topside journals with workflow.

PRD-16: Status workflow (Draft → Pending Approval → Approved → Reversed),
        auto-reversal, approval tracking.
"""
import frappe
from frappe import _
from frappe.model.document import Document
from frappe.model.workflow import get_workflow_name
from frappe.utils import cint, now_datetime

from konsol.period_status import assert_declared, assert_open


class ConsolidationAdjustment(Document):
    # konsol#305 J05: Consolidation Adjustment no longer writes the warehouse.
    # Consolidation Journal is the one writer of
    # epm_staging.consolidation_adjustments; two controllers naming one table
    # erase each other in reconcile_all's TRUNCATE+INSERT (Problems P12).
    # (konsolidat#146 removed the legacy epm_gold write-through earlier.)

    def before_insert(self):
        """Every new adjustment starts in the first state with no approver. An
        amendment or a Duplicate copies status and approver (status isn't
        no_copy, and copy_doc ignores no_copy when amending), and the workflow
        refuses a new doc in any state but its first."""
        self.status = _first_state()
        self.approved_by = self.approved_at = None

    def validate(self):
        """Approved and Reversed are set only by the submit and the cancel. A
        draft saved straight into one (a REST PUT) would look approved, never
        reach the warehouse, and have no transition out.

        The year and period must be declared: an undeclared one is refused
        on every save, before any period-open check."""
        assert_declared(self.fiscal_year, self.fiscal_period)
        if self.docstatus == 0 and self.status and self.status not in _states(0):
            frappe.throw(_("{0} is set by approving or reversing the adjustment, not by saving it.").format(self.status))

    def before_submit(self):
        """Submit IS the approval (decided 12 Sep 2026). The old on_submit
        called self.save() on the submitted doc, an update-after-submit on a
        field that isn't allow_on_submit, so every submit raised (#131).

        Approve only while the period is open: a closed period takes no
        further change, so a draft whose period closed while it waited for
        review is corrected by a new adjustment in an open period (#149)."""
        assert_open(self.fiscal_year, self.fiscal_period, action="approve a consolidation adjustment")
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


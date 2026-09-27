"""Consolidation Journal: a balanced topside or reclassification journal whose
lines each name their entity (konsol#292, #305-D2-1, #305-D2-12).

It replaces Consolidation Adjustment and keeps its lifecycle (Draft ->
Pending Approval -> Approved -> Reversed; submit is the approval, cancel the
reversal). The warehouse sync arrives in J05.
"""
import frappe
from frappe import _
from frappe.model.document import Document
from frappe.model.workflow import get_workflow_name
from frappe.utils import cint, now_datetime

from konsol.close import journal_model
from konsol.period_status import assert_declared, assert_open

#: A blank Link is stored as NULL: match both spellings (business_combination.py:68).
_BLANK = ["is", "not set"]


class ConsolidationJournal(Document):
    def before_insert(self):
        """Every new journal starts in the first state with no approver. An
        amendment or a Duplicate copies status and approver (status isn't
        no_copy, and copy_doc ignores no_copy when amending), and the workflow
        refuses a new doc in any state but its first. A forged insert that
        sets Approved lands here as a draft."""
        self.status = _first_state()
        self.approved_by = self.approved_at = None

    def validate(self):
        """Approved and Reversed are set only by the submit and the cancel. A
        draft saved straight into one (a REST PUT) would look approved and have
        no transition out.

        The year and period must be declared: an undeclared one is refused
        on every save, before any period-open check."""
        assert_declared(self.fiscal_year, self.fiscal_period)
        if self.docstatus == 0 and self.status and self.status not in _states(0):
            frappe.throw(_("{0} is set by approving or reversing the journal, not by saving it.").format(self.status))
        self._validate_lines_and_totals()

    def _validate_lines_and_totals(self):
        """Balance check before send (#305-D2-1): the lines and the total
        must be clean before the totals or the currency are trusted. Exact
        to the cent, in total, not per entity (#305-D2-12; J01's
        balance_problem already sums every line)."""
        lines = [
            {
                "idx": line.idx,
                "main_account": line.main_account,
                "debit_amount": line.debit_amount,
                "credit_amount": line.credit_amount,
            }
            for line in (self.get("lines") or [])
        ]
        found = journal_model.line_problems(lines)
        total_debit, total_credit = journal_model.totals(lines)
        problem = journal_model.balance_problem(total_debit, total_credit)
        if problem:
            found.append(problem)
        if found:
            frappe.throw("<br>".join(found))
        self.total_debit, self.total_credit = total_debit, total_credit

        root = self._root()
        self.currency = root.reporting_currency

        for line in (self.get("lines") or []):
            self._validate_line_entity(line, root)
            self._validate_line_account(line)

    def _root(self):
        """The group node of ``consolidation_group`` (no entity), mirroring
        business_combination.py:293-308 ``_root``."""
        root = frappe.db.get_value(
            "Consolidation Group",
            {"consolidation_group": self.consolidation_group, "data_area_id": _BLANK},
            ["name", "reporting_currency"],
            as_dict=True,
        )
        if not root:
            frappe.throw(
                f"No Consolidation Group '{self.consolidation_group}' root: declare the "
                "group before posting a journal to it."
            )
        return root

    def _validate_line_entity(self, line, root):
        """The line's entity must be a node of this journal's group
        (mirror ownership_period.py:162-170 ``_node``). Presence of the
        entity itself is the line's own ``reqd`` (J02); membership is
        checked here, with the line number."""
        node = frappe.db.get_value(
            "Consolidation Group",
            {"consolidation_group": self.consolidation_group, "data_area_id": line.data_area_id},
            "name",
        )
        if not node:
            frappe.throw(
                f"Line {line.idx}: entity {line.data_area_id} is not in group "
                f"{self.consolidation_group}."
            )

    def _validate_line_account(self, line):
        """The line's account must be a postable (non-group) Published leaf:
        the warehouse chart is Published-only (main_account.py CH_SYNC_FILTERS)."""
        account = frappe.db.get_value(
            "Main Account", line.main_account, ["is_group", "status"], as_dict=True
        )
        if not account:
            frappe.throw(f"Line {line.idx}: Main Account {line.main_account} does not exist.")
        if cint(account.is_group):
            frappe.throw(
                f"Line {line.idx}: {line.main_account} is a group heading and cannot be posted to."
            )
        if account.status != "Published":
            frappe.throw(
                f"Line {line.idx}: {line.main_account} is not Published and cannot be posted to."
            )

    def before_submit(self):
        """Submit IS the approval (decided 12 Sep 2026). Approve only while the
        period is open: a draft whose period closed while it waited for review
        is corrected by a new journal in an open period (#149)."""
        assert_open(self.fiscal_year, self.fiscal_period, action="approve a consolidation journal")
        if get_workflow_name(self.doctype):
            # apply_workflow sets the submitted state before it submits. A
            # direct submit would land a review state at docstatus 1.
            if self.status not in _states(1):
                frappe.throw(_("Approve the journal through its workflow."))
        else:
            self.status = "Approved"
        self.approved_by = frappe.session.user
        self.approved_at = now_datetime()

    def before_cancel(self):
        """Reverse only while the period is open. After close, a correction
        is a NEW journal in an open period."""
        assert_open(self.fiscal_year, self.fiscal_period, action="reverse a consolidation journal")
        if get_workflow_name(self.doctype):
            if self.status not in _states(2):
                frappe.throw(_("Reverse the journal through its workflow."))
        else:
            self.status = "Reversed"


def _workflow():
    name = get_workflow_name("Consolidation Journal")
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

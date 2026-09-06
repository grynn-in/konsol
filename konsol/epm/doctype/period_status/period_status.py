"""Open / Closed / Locked state for one fiscal period in one fiscal year.

Deliberately *not* a field on ``Fiscal Period``. That DocType is a template —
``format:FP-{fiscal_period}`` gives exactly fourteen records (OPN, P1..P12,
CLS) that every fiscal year reuses. Hanging a status off it would mean closing
September 2024 also closed September 2025, silently. Status belongs to the
pair, so it gets its own record.
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import now_datetime

OPEN = "Open"
CLOSED = "Closed"
LOCKED = "Locked"

#: A period that is not Open refuses new work. Locked additionally refuses
#: being reopened by anyone below a System Manager — that is the difference
#: between "we have finished" and "do not touch this again".
SETTLED = (CLOSED, LOCKED)


class PeriodStatus(Document):
    def validate(self):
        self._validate_period_exists()
        self._guard_reopen()
        self._stamp_closure()

    def _validate_period_exists(self):
        """`fiscal_period` is the period *number*, not a Fiscal Period name.

        The whole app passes the integer around — launch_options returns it,
        start_run takes it, the console sends it — while Fiscal Period records
        are named FP-12. A Link field here would store "FP-12" and quietly
        diverge from every other caller, so this is an Int, validated instead.
        """
        if not frappe.db.exists("Fiscal Period", {"fiscal_period": self.fiscal_period}):
            frappe.throw(
                _("No Fiscal Period numbered {0}.").format(self.fiscal_period),
                frappe.ValidationError,
            )

    def _previous_status(self):
        if self.is_new():
            return None
        return frappe.db.get_value("Period Status", self.name, "status")

    def _guard_reopen(self):
        previous = self._previous_status()
        if previous != LOCKED or self.status == LOCKED:
            return
        if "System Manager" not in frappe.get_roles():
            frappe.throw(
                _("{0} is locked. Only a System Manager can reopen it.").format(self.name),
                frappe.PermissionError,
            )

    def _stamp_closure(self):
        """Record who settled the period, and clear it again if reopened."""
        previous = self._previous_status()
        if self.status in SETTLED and previous not in SETTLED:
            self.closed_by = frappe.session.user
            self.closed_on = now_datetime()
        elif self.status == OPEN:
            self.closed_by = None
            self.closed_on = None

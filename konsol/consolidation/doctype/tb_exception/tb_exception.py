"""TB Exception — "no trial balance for <entity> in Pn, because <reason>"
(konsol#303 point 3).

The Close Lead (EPM Admin) declares it; submit is the declaration. The
sign-off completeness gate accepts a submitted exception in place of a
trial balance, so this controller refuses anything that would let the gate
be satisfied wrongly: a blank reason, a group entity, an undeclared or
closed period, an entity-period that already has a submitted trial balance,
and a second exception for the same entity-period.

``declared_by`` is set only in ``before_submit``, to the submitting user.
A value sent on a draft is cleared, so a document never names a declarer
who did not submit it.

Nothing here reaches ClickHouse; the gate reads MariaDB.

Submit and cancel change what the period's checks read, so both record a
data change on the period (konsol#305 A63): a signature over checks that ran
before it stops counting.
"""

import frappe
from frappe.model.document import Document

from konsol.period_status import assert_declared, assert_open


class TBException(Document):

    def validate(self):
        if self.docstatus == 0:
            self.declared_by = None
        assert_declared(self.fiscal_year, self.fiscal_period)
        assert_open(self.fiscal_year, self.fiscal_period,
                    action="declare a trial balance exception")

        if not (self.reason or "").strip():
            frappe.throw(frappe._("Give the reason this entity has no trial balance for the period."))

        if frappe.db.get_value("Entity", self.data_area_id, "is_group"):
            frappe.throw(frappe._(
                "{0} is a group entity; a group never submits a trial balance, "
                "so it needs no exception.").format(self.data_area_id))

        key = {
            "data_area_id": self.data_area_id,
            "fiscal_year": self.fiscal_year,
            "fiscal_period": self.fiscal_period,
            "docstatus": 1,
        }
        code = f"FY{self.fiscal_year} P{self.fiscal_period}"
        tb = frappe.db.get_value("Trial Balance Submission", dict(key), "name", for_update=True)
        if tb:
            frappe.throw(frappe._(
                "A trial balance is already submitted for {0} {1}; an exception is for "
                "a missing one. Cancel {2} first if it should not count.").format(
                    self.data_area_id, code, tb))

        # A locking read: two Close Leads declaring at once must not both pass.
        other = frappe.db.get_value(
            "TB Exception", dict(key, name=["!=", self.name]), "name", for_update=True)
        if other:
            frappe.throw(frappe._(
                "{0} already declares no trial balance for {1} {2}. "
                "Amend it instead of declaring a second one.").format(
                    other, self.data_area_id, code))

    def before_submit(self):
        self.declared_by = frappe.session.user

    def on_submit(self):
        self._record_data_change("submitted")

    def on_cancel(self):
        self._record_data_change("cancelled")

    def _record_data_change(self, what):
        # Imported here: signoff_gate is frappe-bound and reads the fiscal calendar.
        from konsol.close import signoff_gate

        signoff_gate.record_data_change(
            self.fiscal_year, self.fiscal_period,
            "TB Exception %s %s" % (self.name, what), frappe.session.user)

    def before_cancel(self):
        """validate() is not run on cancel, so the period gate is applied here."""
        assert_open(self.fiscal_year, self.fiscal_period,
                    action="cancel a trial balance exception")

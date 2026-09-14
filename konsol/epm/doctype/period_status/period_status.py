"""Period Status: retired, kept read-only as history (konsol#189).

Period open / closed / locked status now lives on the EPM Fiscal Year's period
rows; close, lock and reopen (with the group-rate gate) are actions there, and
konsol/period_status.py reads it. These records stay only so the history can be
read, until a later PR deletes the doctype. Nothing may write one, except a
migration patch.
"""

import frappe
from frappe import _
from frappe.model.document import Document

RETIRED = "Period Status is retired; set period status on the EPM Fiscal Year"


class PeriodStatus(Document):
    def before_insert(self):
        _refuse()

    def validate(self):
        _refuse()


def _refuse():
    if frappe.flags.in_patch:
        return
    frappe.throw(_(RETIRED), frappe.ValidationError)

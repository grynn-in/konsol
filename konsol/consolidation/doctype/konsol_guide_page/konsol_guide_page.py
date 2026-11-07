"""Konsol Guide Page: a site's own page in the Konsol Handbook (konsol#407 phase 3).

The product guide ships as markdown in the app (konsol/handbook_pages/) and
changes with every deploy; these records are the site's (Handbook option C,
Deepak Pai, 7 Nov 2026). Who may read a draft is konsol.handbook's
(hooks.py); how a page is shown, and its sanitising, konsol.handbook_model's.
"""
import frappe
from frappe.model.document import Document


class KonsolGuidePage(Document):
    def validate(self):
        for field in ("title", "section"):
            value = (self.get(field) or "").strip()
            if not value:
                frappe.throw(frappe._("{0} cannot be blank.").format(self.meta.get_label(field)))
            self.set(field, value)
        if not (self.body or "").strip():
            frappe.throw(frappe._("Body cannot be blank."))

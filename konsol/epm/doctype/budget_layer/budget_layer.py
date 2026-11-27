"""Budget Layer: a named layer of budget numbers (base, challenge, management,
board, or whatever a site declares). The writer role is read by the budget
intake (konsol#348).

validate refuses a layer code the K.EPM readers would refuse (konsol#564): the
code is the document name and the value the readers bind, and they accept one
rule only (konsol.identifier_rule). A site that already holds a layer breaking
the rule keeps it; the readers keep refusing it until it is recreated."""
import frappe
from frappe import _
from frappe.model.document import Document

from konsol.identifier_rule import SAFE_IDENTIFIER, SAFE_IDENTIFIER_RULE


class BudgetLayer(Document):
    def validate(self):
        code = self.layer_code or ""
        if not SAFE_IDENTIFIER.match(code):
            frappe.throw(
                _("Layer Code {0} is not allowed: a layer code must be {1}. K.EPM refuses any other code as "
                  "an invalid layer.").format(code, SAFE_IDENTIFIER_RULE),
                frappe.ValidationError)

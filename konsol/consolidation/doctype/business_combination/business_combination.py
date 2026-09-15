"""Business Combination: one document per acquisition of control (konsolidat#198).

The deal is a declared input; the policy it is measured under lives on the
Consolidation Group root. The purchase price allocation (validate), the
Ownership Period it creates (on_submit) and the warehouse write-through are
added in a later row.
"""
from frappe.model.document import Document


class BusinessCombination(Document):
    pass

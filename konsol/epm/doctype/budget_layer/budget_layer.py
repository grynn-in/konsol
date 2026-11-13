"""Budget Layer: a named layer of budget numbers (base, challenge, management,
board, or whatever a site declares). No behaviour: the writer role is read by
the budget intake (konsol#348)."""
from frappe.model.document import Document


class BudgetLayer(Document):
    pass

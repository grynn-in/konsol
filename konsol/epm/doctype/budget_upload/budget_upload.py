"""Budget Upload: one bulk budget file (many entities, layers and periods) and
what became of it. Mirrors Trial Balance Upload (konsol#348).

Status, counts and the report are written by the check and the load (U1, U2),
never typed: every field but the file and the amount basis is read-only. The
load writes one Budget Entry per (cycle, entity, layer, period). The derived
form indicator (`status_now`) comes with the load, which knows what still
stands."""
from frappe.model.document import Document


class BudgetUpload(Document):
    pass

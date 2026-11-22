"""Rate Sheet (konsol#592 pair 1). T6 stub: the rules arrive in T8 and T9."""
from frappe.model.document import Document

from konsol.workflows import set_cancelled_state


class RateSheet(Document):
    def before_cancel(self):
        set_cancelled_state(self)

"""Budget Upload: one bulk budget file (many entities, layers and periods) and
what became of it. Mirrors Trial Balance Upload (konsol#348).

Status, counts and the report are written by the check and the load
(konsol/budget_bulk.py, U1 and U2), never typed: every field but the file and
the amount basis is read-only. The load writes one Budget Entry per (cycle,
entity, layer, period)."""
from frappe.model.document import Document


class BudgetUpload(Document):
    @property
    def loaded_count(self):
        """Virtual: the Budget Entries this upload loaded that still name it
        (the trial balance upload's rule, konsol#332-2). Never stored; a
        later upload that replaces an entry lowers this count when read."""
        from konsol.budget_bulk import upload_state

        return upload_state(self)["loaded_count"]

    def onload(self):
        """The form's indicator is the upload as it stands now, sent as
        `status_now`: a load whose entries were all replaced since reads
        Superseded. The stored status is the load job's own state."""
        from konsol.budget_bulk import upload_state

        self.set_onload("status_now", upload_state(self)["status"])

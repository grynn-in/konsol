from frappe.model.document import Document


class TrialBalanceUpload(Document):
    """One bulk trial balance file and what became of it.

    Status, counts and the report are written by konsol/tb_bulk.py (check,
    then load), never typed: every field but the file is read-only. Each
    ready entity-period becomes an ordinary Trial Balance Submission, which
    is where the rules live.
    """

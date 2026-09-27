from frappe.model.document import Document


class ConsolidationJournalLine(Document):
    """One entity, account and debit/credit pair on a Consolidation Journal
    (konsol#292, #305-D2-12). The parent syncs both halves of the row to the
    warehouse together (J05); this child holds no logic of its own."""

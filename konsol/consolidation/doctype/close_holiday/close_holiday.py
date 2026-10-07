from frappe.model.document import Document


class CloseHoliday(Document):
    """One group-wide holiday on Close Settings (konsol#305 D53, decision
    #305-2.4-1). Uniqueness is checked at save by deadline_model.rule_problems;
    not synced to ClickHouse."""

from frappe.model.document import Document


class CloseHoliday(Document):
    """One group-wide holiday on a Close Holiday Calendar (konsol#305 D53,
    decision #305-2.4-1; moved from Close Settings by konsol#509). Uniqueness
    and the year are checked at save by deadline_model.calendar_problems; not
    synced to ClickHouse."""

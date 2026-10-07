from frappe.model.document import Document


class CloseDeadlineRule(Document):
    """One effective-dated deadline rule on Close Settings (konsol#305 D53,
    decision #305-2.4-1). The save rules live in close/deadline_model.py
    (rule_problems), called by Close Settings; not synced to ClickHouse."""

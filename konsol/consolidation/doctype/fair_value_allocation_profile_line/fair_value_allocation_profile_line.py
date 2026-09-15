from frappe.model.document import Document


class FairValueAllocationProfileLine(Document):
    """One account and its weight in a Fair Value Allocation Profile
    (konsol#208); the parent validates the lines together."""

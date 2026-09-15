from frappe.model.document import Document


class BusinessCombinationCost(Document):
    """One acquisition-related cost (expensed or capitalised per the group's
    policy); synced by (parent, idx) with the parent Business Combination."""

    CH_TABLE = "epm_staging.business_combination_costs"
    CH_FIELD_MAP = {
        "parent": "parent",
        "idx": "idx",
        "kind": "kind",
        "amount": "amount",
        "currency": "currency",
        "description": "description",
    }
    # Only approved deals' lines reach the warehouse (see Consideration).
    CH_SYNC_FILTERS = {"docstatus": 1}

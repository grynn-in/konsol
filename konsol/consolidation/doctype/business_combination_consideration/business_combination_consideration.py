from frappe.model.document import Document


class BusinessCombinationConsideration(Document):
    """One component of the consideration transferred; synced by (parent, idx)
    when the parent Business Combination is submitted, cancelled or deleted."""

    CH_TABLE = "epm_staging.business_combination_consideration"
    CH_FIELD_MAP = {
        "parent": "parent",
        "idx": "idx",
        "component": "component",
        "amount": "amount",
        "currency": "currency",
        "settlement_date": "settlement_date",
        "description": "description",
    }
    # A child row carries its parent's docstatus, and resolve_sync_filters
    # keys on is_submittable (which a child table is not): say it here, so
    # only approved deals' lines reach the warehouse, like the header.
    CH_SYNC_FILTERS = {"docstatus": 1}

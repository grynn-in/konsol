from frappe.model.document import Document


class CostAllocationStatisticValue(Document):
    """One driver value for a (period, member); synced by (parent, idx) with
    the parent Cost Allocation Statistic."""

    CH_TABLE = "epm_staging.cost_allocation_statistic_values"
    CH_FIELD_MAP = {
        "parent": "parent",
        "idx": "idx",
        "fiscal_period": "fiscal_period",
        "member": "member",
        "value": "value",
    }
    # A child row carries its parent's docstatus, and resolve_sync_filters
    # keys on is_submittable (which a child table is not): say it here, so
    # only submitted statistics' rows reach the warehouse, like the header.
    CH_SYNC_FILTERS = {"docstatus": 1}

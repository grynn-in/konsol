from frappe.model.document import Document


class CostAllocationRunWeight(Document):
    """One weight row of a Cost Allocation Run: written by Compute Weights, read-only.
    Synced by (parent, idx) with the submitted parent run (K8)."""

    CH_TABLE = "epm_staging.cost_allocation_run_weights"
    CH_FIELD_MAP = {
        "parent": "parent",
        "idx": "idx",
        "step": "step",
        "sender_member": "sender_member",
        "receiver_member": "receiver_member",
        "driver": "driver",
        "statistic": "statistic",
        "value_period": "value_period",
        "carried_forward": "carried_forward",
        "driver_value": "driver_value",
    }
    # A child row carries its parent's docstatus; resolve_sync_filters keys on
    # is_submittable, which a child table is not, so say it here.
    CH_SYNC_FILTERS = {"docstatus": 1}

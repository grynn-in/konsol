from frappe.model.document import Document


class CostAllocationRunDriverAccount(Document):
    """One account of an Account Set driver, snapshotted on a Cost Allocation
    Run at Compute (konsol#269 phase 2b-1), read-only. Synced by (parent, idx)
    with the submitted parent run, like the run's weights and accounts."""

    CH_TABLE = "epm_staging.cost_allocation_run_driver_accounts"
    CH_FIELD_MAP = {
        "parent": "parent",
        "idx": "idx",
        "driver": "driver",
        "main_account": "main_account",
        "value_sign": "value_sign",
    }
    CH_SYNC_FILTERS = {"docstatus": 1}

from frappe.model.document import Document


class CostAllocationRunAccount(Document):
    """One expanded cost pool account of a Cost Allocation Run, read-only.
    Synced by (parent, idx) with the submitted parent run (K8)."""

    CH_TABLE = "epm_staging.cost_allocation_run_accounts"
    CH_FIELD_MAP = {
        "parent": "parent",
        "idx": "idx",
        "main_account": "main_account",
        "declared_account": "declared_account",
    }
    CH_SYNC_FILTERS = {"docstatus": 1}

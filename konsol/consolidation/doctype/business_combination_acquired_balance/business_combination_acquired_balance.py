from frappe.model.document import Document


class BusinessCombinationAcquiredBalance(Document):
    """One line of the acquired balance sheet (Dr +, Cr −) with its fair value
    step-up; synced by (parent, idx) with the parent Business Combination."""

    CH_TABLE = "epm_staging.business_combination_acquired_balances"
    CH_FIELD_MAP = {
        "parent": "parent",
        "idx": "idx",
        "main_account": "main_account",
        "book_amount": "book_amount",
        "fair_value_adjustment": "fair_value_adjustment",
        "note": "note",
    }
    # Only approved deals' lines reach the warehouse (see Consideration).
    CH_SYNC_FILTERS = {"docstatus": 1}

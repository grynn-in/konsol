

def get_data():
    return {
        "fieldname": "fiscal_year",
        "transactions": [
            {
                "label": "Close",
                "items": ["Trial Balance Submission", "TB Exception", "Group Exchange Rate", "Assertion Run"],
            },
            {
                "label": "Consolidation",
                "items": ["Consolidation Journal", "Consolidation Adjustment", "IC Balance"],
            },
        ],
    }



def get_data():
    return {
        "fieldname": "fiscal_year",
        "transactions": [
            {
                "label": "Close",
                "items": ["Trial Balance Submission", "TB Exception", "Group Exchange Rate", "Rate Sheet", "Assertion Run"],
            },
            {
                "label": "Consolidation",
                "items": ["Consolidation Journal", "IC Balance"],
            },
            {
                "label": "Budget",
                "items": ["Budget Entry"],
            },
            {
                "label": "Allocation",
                "items": ["Cost Allocation Run"],
            },
        ],
    }


def get_data():
    return {
        "fieldname": "cycle",
        "non_standard_fieldnames": {
            "Budget Sheet": "cycle",
        },
        "transactions": [
            {"label": "Budget", "items": ["Budget Sheet"]},
        ],
    }
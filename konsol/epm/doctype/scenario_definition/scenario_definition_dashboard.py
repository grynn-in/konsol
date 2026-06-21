
def get_data():
    return {
        "fieldname": "scenario_id",
        "non_standard_fieldnames": {
            "Budget Cycle": "scenario_id",
        },
        "transactions": [
            {"label": "Budget", "items": ["Budget Cycle"]},
        ],
    }
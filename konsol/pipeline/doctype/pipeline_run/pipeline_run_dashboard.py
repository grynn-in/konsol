
def get_data():
    return {
        "fieldname": "pipeline_run",
        "non_standard_fieldnames": {
            "Close Run": "pipeline_run",
        },
        "transactions": [
            {"label": "Consolidation", "items": ["Close Run"]},
        ],
    }
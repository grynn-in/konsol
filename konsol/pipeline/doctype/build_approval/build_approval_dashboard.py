
def get_data():
    return {
        "fieldname": "build_approval",
        "method": "konsol.desk.connections.get_open_count",
        "non_standard_fieldnames": {
            "Pipeline Run": "build_approval",
        },
        "transactions": [
            {"label": "Runs", "items": ["Pipeline Run"]},
        ],
    }
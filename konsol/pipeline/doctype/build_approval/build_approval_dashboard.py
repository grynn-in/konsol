
def get_data():
    return {
        "fieldname": "pipeline_build_request",
        "method": "konsol.desk.connections.get_open_count",
        "non_standard_fieldnames": {
            "Pipeline Run": "pipeline_build_request",
        },
        "transactions": [
            {"label": "Runs", "items": ["Pipeline Run"]},
        ],
    }
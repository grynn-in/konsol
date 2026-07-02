
def get_data():
    return {
        "fieldname": "pipeline_run",
        "non_standard_fieldnames": {
            "Assertion Run": "pipeline_run",
        },
        "internal_links": {
            "Build Approval": "pipeline_build_request",
        },
        "transactions": [
            {"label": "Governance", "items": ["Build Approval"]},
            {"label": "Consolidation", "items": ["Assertion Run"]},
        ],
    }
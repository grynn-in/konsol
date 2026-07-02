
def get_data():
    return {
        "fieldname": "pipeline_run",
        "non_standard_fieldnames": {
            "Assertion Run": "pipeline_run",
        },
        "internal_links": {
            "Build Approval": "build_approval",
        },
        "transactions": [
            {"label": "Governance", "items": ["Build Approval"]},
            {"label": "Consolidation", "items": ["Assertion Run"]},
        ],
    }
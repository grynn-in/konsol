
def get_data():
    return {
        "fieldname": "pipeline_run",
        "non_standard_fieldnames": {
            "Close Run": "pipeline_run",
        },
        "internal_links": {
            "Pipeline Build Request": "pipeline_build_request",
        },
        "transactions": [
            {"label": "Governance", "items": ["Pipeline Build Request"]},
            {"label": "Consolidation", "items": ["Close Run"]},
        ],
    }
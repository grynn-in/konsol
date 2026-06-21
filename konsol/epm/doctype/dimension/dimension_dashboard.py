
def get_data():
    return {
        "fieldname": "dimension",
        "non_standard_fieldnames": {
            "Dimension Mapping": "dimension",
            "Reporting Hierarchy": "dimension",
        },
        "transactions": [
            {"label": "Mappings", "items": ["Dimension Mapping"]},
            {"label": "Reporting", "items": ["Reporting Hierarchy"]},
        ],
    }
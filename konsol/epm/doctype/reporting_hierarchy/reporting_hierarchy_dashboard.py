
def get_data():
    return {
        "fieldname": "reporting_hierarchy",
        "non_standard_fieldnames": {
            "Reporting Hierarchy Member": "reporting_hierarchy",
        },
        "internal_links": {
            "Dimension": "dimension",
        },
        "transactions": [
            {"label": "Hierarchy", "items": ["Reporting Hierarchy Member"]},
            {"label": "Reference", "items": ["Dimension"]},
        ],
    }

def get_data():
    return {
        "fieldname": "connector",
        "non_standard_fieldnames": {
            "Connector Health": "connector",
        },
        "internal_links": {
            "Dimension": ["dimension_mappings", "dimension"],
        },
        "transactions": [
            {"label": "Health", "items": ["Connector Health"]},
            {"label": "Configuration", "items": ["Dimension"]},
        ],
    }
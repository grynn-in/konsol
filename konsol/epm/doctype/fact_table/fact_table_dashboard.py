
def get_data():
    return {
        "internal_links": {
            "Measure": ["fact_measures", "measure"],
            "Dimension": ["fact_dimensions", "dimension"],
        },
        "transactions": [
            {"label": "Registry", "items": ["Measure", "Dimension"]},
        ],
    }
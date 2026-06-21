_CONSOLIDATION_CHILDREN = [
    "Ownership Period",
    "Historical Equity Rate",
    "Consolidation Adjustment",
]


def get_data():
    return {
        "fieldname": "parent_consolidation_group",
        "method": "konsol.desk.connections.get_open_count",
        "non_standard_fieldnames": {
            "Consolidation Group": "parent_consolidation_group",
        },
        "transactions": [
            {"label": "Structure", "items": ["Consolidation Group"]},
            {"label": "Consolidation", "items": _CONSOLIDATION_CHILDREN},
        ],
    }
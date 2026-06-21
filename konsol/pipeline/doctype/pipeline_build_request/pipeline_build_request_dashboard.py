_TRIGGER_DOCTYPES = [
    "Consolidation Group",
    "Consolidation Adjustment",
    "Ownership Period",
    "Historical Equity Rate",
    "IC Elimination Rule",
    "IC Balance",
    "Allocation Rule",
    "Allocation Driver",
    "Allocation Run",
]


def get_data():
    return {
        "fieldname": "name",
        "method": "konsol.desk.connections.get_open_count",
        "transactions": [
            {"label": "Trigger", "items": _TRIGGER_DOCTYPES},
        ],
    }
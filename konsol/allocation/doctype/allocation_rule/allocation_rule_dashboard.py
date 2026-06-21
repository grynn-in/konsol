
def get_data():
    return {
        "fieldname": "driver_type",
        "method": "konsol.desk.connections.get_open_count",
        "transactions": [
            {"label": "Drivers", "items": ["Allocation Driver"]},
        ],
    }
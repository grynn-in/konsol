
def get_data():
    """Static shell only — per-doc trigger links are added in pipeline_build_request.js."""
    return {
        "fieldname": "name",
        "method": "konsol.desk.connections.get_open_count",
        "transactions": [],
    }
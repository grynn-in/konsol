"""TDD tests for EPM Settings DocType."""
import json
import os

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_epm_settings_json_exists():
    """EPM Settings DocType JSON must exist."""
    path = os.path.join(
        APP_DIR, "pipeline", "doctype", "epm_settings", "epm_settings.json"
    )
    assert os.path.exists(path), f"Missing: {path}"


def test_epm_settings_json_valid():
    """EPM Settings JSON must be valid and have required fields."""
    path = os.path.join(
        APP_DIR, "pipeline", "doctype", "epm_settings", "epm_settings.json"
    )
    with open(path) as f:
        doc = json.load(f)

    assert doc["name"] == "EPM Settings"
    assert doc["doctype"] == "DocType"
    assert doc["issingle"] == 1
    assert doc["module"] == "Pipeline"

    field_names = [f["fieldname"] for f in doc["fields"]]
    required_fields = [
        "clickhouse_host",
        "clickhouse_port",
        "clickhouse_user",
        "clickhouse_password",
        "airbyte_api_url",
        "airbyte_connection_id",
        "airbyte_client_id",
        "airbyte_client_secret",
        "dbt_project_path",
    ]
    for fname in required_fields:
        assert fname in field_names, f"Missing field: {fname}"


def test_epm_settings_password_fields():
    """Password fields must have fieldtype Password."""
    path = os.path.join(
        APP_DIR, "pipeline", "doctype", "epm_settings", "epm_settings.json"
    )
    with open(path) as f:
        doc = json.load(f)

    password_fields = {"clickhouse_password", "airbyte_client_secret"}
    for field in doc["fields"]:
        if field["fieldname"] in password_fields:
            assert field["fieldtype"] == "Password", (
                f"{field['fieldname']} must be Password type"
            )


def test_epm_settings_python_exists():
    """EPM Settings Python file must exist."""
    path = os.path.join(
        APP_DIR, "pipeline", "doctype", "epm_settings", "epm_settings.py"
    )
    assert os.path.exists(path), f"Missing: {path}"

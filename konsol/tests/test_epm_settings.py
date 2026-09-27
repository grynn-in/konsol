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
        "airbyte_workspace_id",
        "airbyte_destination_id",
        "airbyte_clickhouse_host",
        "airbyte_clickhouse_port",
        "airbyte_clickhouse_database",
        "airbyte_d365_source_definition_id",
        "airbyte_erpnext_source_definition_id",
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


def test_epm_settings_d365_section_marked_legacy():
    path = os.path.join(
        APP_DIR, "pipeline", "doctype", "epm_settings", "epm_settings.json"
    )
    with open(path) as f:
        doc = json.load(f)

    section = next(
        f for f in doc["fields"] if f["fieldname"] == "d365_writeback_section"
    )
    assert "Legacy" in section["label"]


def test_epm_settings_python_exists():
    """EPM Settings Python file must exist."""
    path = os.path.join(
        APP_DIR, "pipeline", "doctype", "epm_settings", "epm_settings.py"
    )
    assert os.path.exists(path), f"Missing: {path}"


def test_no_consolidation_currency_setting():
    """konsolidat#93 (decided 13 Sep 2026): the presentation currency lives on
    each Consolidation Group node. The EPM Settings field was read by nothing,
    so a value set there changed nothing: it is gone, with its getter and the
    Consolidation tab that held only it."""
    path = os.path.join(
        APP_DIR, "pipeline", "doctype", "epm_settings", "epm_settings.json"
    )
    with open(path) as f:
        doc = json.load(f)
    names = {f["fieldname"] for f in doc["fields"]}
    assert not names & {"consolidation_currency", "group_reporting_section", "tab_consolidation"}

    with open(os.path.join(
        APP_DIR, "pipeline", "doctype", "epm_settings", "epm_settings.py"
    )) as f:
        src = f.read()
    assert "get_consolidation_currency" not in src
    assert "DEFAULT_CONSOLIDATION_CURRENCY" not in src


def test_no_first_close_fields_in_epm_settings():
    """konsol#305 A36b: A06 put the first close period on EPM Settings
    (System Manager only), then A36 proved live that field-level permlevel
    write cannot let the Close Lead save just those fields — Frappe's base
    write check reads permlevel-0 rows only. A36a moved the fields to their
    own doctype, Close Settings (EPM Admin + System Manager write), so this
    reverts A06's additions here."""
    path = os.path.join(
        APP_DIR, "pipeline", "doctype", "epm_settings", "epm_settings.json"
    )
    with open(path) as f:
        doc = json.load(f)
    names = {f["fieldname"] for f in doc["fields"]}
    assert not names & {
        "close_order_section", "first_close_fiscal_year", "first_close_fiscal_period",
    }
    assert "close_order_section" not in doc["field_order"]
    assert "first_close_fiscal_year" not in doc["field_order"]
    assert "first_close_fiscal_period" not in doc["field_order"]

    with open(os.path.join(
        APP_DIR, "pipeline", "doctype", "epm_settings", "epm_settings.py"
    )) as f:
        src = f.read()
    assert "validate_first_close_period" not in src
    assert "first_close_fiscal_year" not in src

"""Unit tests for Airbyte provisioning service."""
import os
import sys
import types
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

if "frappe" not in sys.modules:
    fake_frappe_module = types.ModuleType("frappe")
    fake_frappe_module.db = MagicMock()
    fake_frappe_module.get_doc = MagicMock()
    fake_frappe_module.get_single = MagicMock()
    fake_frappe_module.throw = MagicMock()
    sys.modules["frappe"] = fake_frappe_module

import konsol.airbyte_service as module

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _connector_doc():
    doc = SimpleNamespace(
        name="CONN-00001",
        connector_name="D365 F&O Production",
        erp_type="d365_fo",
        tenant_id="tenant",
        environment_url="https://example.operations.dynamics.com",
        extract_client_id="extract-id",
        extract_page_size=100,
        extract_cross_company=1,
        host_url="",
        extract_api_key="",
        writeback_enabled=0,
        writeback_credentials_separate=0,
        get_password=lambda field, raise_exception=False: {
            "extract_client_secret": "extract-secret",
        }.get(field, ""),
    )
    doc.db_set = MagicMock()
    return doc


def test_airbyte_client_authenticate_parses_token():
    client = module.AirbyteClient("http://localhost:8000", "cid", "csecret")
    with patch("konsol.airbyte_service.requests.post") as mock_post:
        mock_post.return_value = MagicMock(
            status_code=200,
            json=MagicMock(return_value={"access_token": "abc"}),
        )
        client.authenticate()
    assert client._token == "abc"


def test_resolve_source_definition_uses_settings_override():
    client = MagicMock()
    settings = {
        "d365_source_definition_id": "def-123",
        "erpnext_source_definition_id": "",
    }
    assert (
        module._resolve_source_definition_id(client, "ws-1", "d365_fo", settings)
        == "def-123"
    )


def test_find_or_create_connection_reuses_existing_pair():
    client = MagicMock()
    client.list_connections.return_value = [
        {
            "connectionId": "conn-1",
            "sourceId": "src-1",
            "destinationId": "dst-1",
        }
    ]
    conn_id = module._find_or_create_connection(
        client, "ws-1", "src-1", "dst-1", "D365 F&O Production"
    )
    assert conn_id == "conn-1"
    client.create_connection.assert_not_called()


def test_provision_connector_airbyte_updates_connector_ids():
    doc = _connector_doc()

    with patch("frappe.get_doc", return_value=doc), patch(
        "frappe.db.commit"
    ), patch.object(module, "check_extract_connection", return_value=(True, "ok")), patch.object(
        module,
        "get_airbyte_settings",
        return_value={
            "api_url": "http://localhost:8000",
            "client_id": "cid",
            "client_secret": "csecret",
            "workspace_id": "ws-1",
            "destination_id": "dst-1",
            "clickhouse_host": "172.30.0.10",
            "clickhouse_port": 8123,
            "clickhouse_database": "epm_raw",
            "clickhouse_user": "default",
            "clickhouse_password": "pw",
            "d365_source_definition_id": "def-d365",
            "erpnext_source_definition_id": "",
        },
    ), patch.object(module, "AirbyteClient") as mock_client_cls:
        client = MagicMock()
        client.authenticate.return_value = None
        client.list_destinations.return_value = []
        client.list_sources.return_value = []
        client.create_source.return_value = "src-1"
        client.list_connections.return_value = []
        client.create_connection.return_value = "conn-1"
        mock_client_cls.return_value = client

        result = module.provision_connector_airbyte("CONN-00001")

    assert result["airbyte_source_id"] == "src-1"
    assert result["airbyte_connection_id"] == "conn-1"
    doc.db_set.assert_any_call("airbyte_source_id", "src-1", update_modified=False)
    doc.db_set.assert_any_call("airbyte_connection_id", "conn-1", update_modified=False)


def test_provision_connector_airbyte_fails_extract_check():
    doc = _connector_doc()

    with patch("frappe.get_doc", return_value=doc), patch(
        "frappe.throw", side_effect=lambda msg: (_ for _ in ()).throw(RuntimeError(msg))
    ), patch.object(module, "check_extract_connection", return_value=(False, "bad creds")):
        with pytest.raises(RuntimeError, match="bad creds"):
            module.provision_connector_airbyte("CONN-00001")


def test_connector_has_airbyte_actions():
    src = open(os.path.join(APP_DIR, "pipeline", "doctype", "connector", "connector.py")).read()
    assert "def test_extract_connection" in src
    assert "def provision_airbyte" in src


def test_cli_api_exposes_airbyte_methods():
    src = open(os.path.join(APP_DIR, "cli_api.py")).read()
    assert "test_connector_extract_api" in src
    assert "provision_connector_airbyte_api" in src
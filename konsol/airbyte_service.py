"""Provision and test Airbyte sources/connections from Connector extract profiles."""
from __future__ import annotations

import logging

import requests

from konsol.connector_credentials import D365_ERP_TYPES, ERP_NEXT_ERP_TYPES, build_extract_config
from konsol.connector_credentials import build_writeback_config
from konsol.extract_check import check_extract_connection, check_writeback_connection

logger = logging.getLogger(__name__)

DESTINATION_NAME = "Open EPM ClickHouse (konsol)"
CLICKHOUSE_DESTINATION_TYPE = "clickhouse"

ERP_AIRBYTE_SOURCE = {
    "d365_fo": {
        "definition_name": "D365 Finance & Operations",
        "legacy_source_type": "d365-fno",
    },
    "d365_bc": {
        "definition_name": "D365 Business Central",
        "legacy_source_type": "d365-bc",
    },
    "erpnext": {
        "definition_name": "ERPNext",
        "legacy_source_type": "erpnext",
    },
}


class AirbyteError(Exception):
    """Raised when Airbyte API calls fail."""


class AirbyteClient:
    """Minimal Airbyte public API client (self-hosted or Cloud)."""

    def __init__(self, api_url, client_id, client_secret):
        self.base_url = api_url.rstrip("/") + "/api/v1"
        self.client_id = client_id
        self.client_secret = client_secret
        self._token = None

    def authenticate(self):
        resp = requests.post(
            f"{self.base_url}/applications/token",
            json={
                "client_id": self.client_id,
                "client_secret": self.client_secret,
            },
            timeout=30,
        )
        if resp.status_code >= 400:
            raise AirbyteError(
                f"Airbyte authentication failed (HTTP {resp.status_code}). "
                "Check airbyte_api_url, airbyte_client_id, and airbyte_client_secret "
                "in EPM Settings."
            )
        payload = resp.json()
        self._token = payload.get("access_token")
        if not self._token:
            raise AirbyteError("Airbyte token response missing access_token.")

    def _headers(self):
        if not self._token:
            self.authenticate()
        return {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
        }

    def request(self, method, path, *, params=None, json_body=None):
        url = f"{self.base_url}{path}"
        resp = requests.request(
            method,
            url,
            headers=self._headers(),
            params=params,
            json=json_body,
            timeout=60,
        )
        if resp.status_code >= 400:
            detail = resp.text[:500]
            raise AirbyteError(
                f"Airbyte API {method} {path} failed (HTTP {resp.status_code}): {detail}"
            )
        if not resp.content:
            return {}
        return resp.json()

    def list_workspaces(self):
        payload = self.request("GET", "/workspaces")
        return payload.get("data") or payload.get("workspaces") or []

    def list_sources(self, workspace_id):
        payload = self.request(
            "GET",
            "/sources",
            params={"workspaceIds": workspace_id, "limit": 100},
        )
        return payload.get("data") or []

    def list_destinations(self, workspace_id):
        payload = self.request(
            "GET",
            "/destinations",
            params={"workspaceIds": workspace_id, "limit": 100},
        )
        return payload.get("data") or []

    def list_connections(self, workspace_id):
        payload = self.request(
            "GET",
            "/connections",
            params={"workspaceIds": workspace_id, "limit": 100},
        )
        return payload.get("data") or []

    def list_source_definitions(self, workspace_id):
        for path in (
            f"/workspaces/{workspace_id}/definitions/sources",
            "/source_definitions",
        ):
            try:
                payload = self.request("GET", path)
            except AirbyteError:
                continue
            rows = payload.get("data") or payload.get("sourceDefinitions") or []
            if rows:
                return rows
        return []

    def create_source(self, *, workspace_id, name, definition_id, configuration):
        body = {
            "name": name,
            "workspaceId": workspace_id,
            "definitionId": definition_id,
            "configuration": configuration,
        }
        payload = self.request("POST", "/sources", json_body=body)
        return payload.get("sourceId") or payload.get("source_id")

    def patch_source(self, source_id, *, name, workspace_id, configuration):
        body = {
            "name": name,
            "workspaceId": workspace_id,
            "configuration": configuration,
        }
        self.request("PATCH", f"/sources/{source_id}", json_body=body)
        return source_id

    def create_destination(self, *, workspace_id, name, definition_id, configuration):
        body = {
            "name": name,
            "workspaceId": workspace_id,
            "definitionId": definition_id,
            "configuration": configuration,
        }
        payload = self.request("POST", "/destinations", json_body=body)
        return payload.get("destinationId") or payload.get("destination_id")

    def create_connection(self, *, source_id, destination_id, name):
        body = {
            "name": name,
            "sourceId": source_id,
            "destinationId": destination_id,
            "status": "active",
            "schedule": {"scheduleType": "manual"},
            "namespaceDefinition": "destination",
        }
        payload = self.request("POST", "/connections", json_body=body)
        return payload.get("connectionId") or payload.get("connection_id")


def get_airbyte_settings():
    """Read Airbyte + ClickHouse destination settings from EPM Settings."""
    import frappe

    settings = frappe.get_single("EPM Settings")
    return {
        "api_url": (settings.airbyte_api_url or "").rstrip("/"),
        "client_id": settings.airbyte_client_id or "",
        "client_secret": settings.get_password("airbyte_client_secret", raise_exception=False)
        or "",
        "workspace_id": settings.airbyte_workspace_id or "",
        "destination_id": settings.airbyte_destination_id or "",
        "clickhouse_host": settings.airbyte_clickhouse_host or "172.30.0.10",
        "clickhouse_port": int(settings.airbyte_clickhouse_port or 8123),
        "clickhouse_database": settings.airbyte_clickhouse_database or "epm_raw",
        "clickhouse_user": settings.clickhouse_user or "default",
        "clickhouse_password": settings.get_password("clickhouse_password", raise_exception=False)
        or "",
        "d365_source_definition_id": settings.airbyte_d365_source_definition_id or "",
        "erpnext_source_definition_id": settings.airbyte_erpnext_source_definition_id or "",
    }


def require_airbyte_settings(settings):
    import frappe

    missing = [
        key
        for key, value in {
            "airbyte_api_url": settings["api_url"],
            "airbyte_client_id": settings["client_id"],
            "airbyte_client_secret": settings["client_secret"],
        }.items()
        if not value
    ]
    if missing:
        frappe.throw(
            "Airbyte is not configured in EPM Settings. Missing: "
            + ", ".join(missing)
        )


def _connector_source_name(connector_name):
    return f"konsol: {connector_name}"


def _connection_name(connector_name):
    return f"konsol: {connector_name} -> ClickHouse"


def _resolve_workspace_id(client, settings):
    if settings["workspace_id"]:
        return settings["workspace_id"]
    workspaces = client.list_workspaces()
    if not workspaces:
        raise AirbyteError(
            "No Airbyte workspaces found. Set airbyte_workspace_id in EPM Settings."
        )
    first = workspaces[0]
    return first.get("workspaceId") or first.get("workspace_id") or first.get("id")


def _resolve_source_definition_id(client, workspace_id, erp_type, settings):
    if erp_type in D365_ERP_TYPES and settings["d365_source_definition_id"]:
        return settings["d365_source_definition_id"]
    if erp_type in ERP_NEXT_ERP_TYPES and settings["erpnext_source_definition_id"]:
        return settings["erpnext_source_definition_id"]

    meta = ERP_AIRBYTE_SOURCE.get(erp_type)
    if not meta:
        raise AirbyteError(f"No Airbyte source mapping for erp_type '{erp_type}'.")

    target_name = meta["definition_name"].lower()
    for row in client.list_source_definitions(workspace_id):
        name = (row.get("name") or "").lower()
        if name == target_name or target_name in name:
            return row.get("definitionId") or row.get("sourceDefinitionId") or row.get("id")

    raise AirbyteError(
        f"Airbyte source definition '{meta['definition_name']}' was not found. "
        "Load the custom connector in Airbyte Connector Builder, then set "
        "airbyte_d365_source_definition_id or airbyte_erpnext_source_definition_id "
        "in EPM Settings."
    )


def _resolve_destination_definition_id(client, workspace_id):
    for path in (
        f"/workspaces/{workspace_id}/definitions/destinations",
        "/destination_definitions",
    ):
        try:
            payload = client.request("GET", path)
        except AirbyteError:
            continue
        rows = payload.get("data") or payload.get("destinationDefinitions") or []
        for row in rows:
            name = (row.get("name") or "").lower()
            if "clickhouse" in name:
                return row.get("definitionId") or row.get("destinationDefinitionId") or row.get("id")
    raise AirbyteError(
        "ClickHouse destination definition not found in Airbyte. "
        "Install the ClickHouse destination or set airbyte_destination_id in EPM Settings."
    )


def _clickhouse_destination_configuration(settings):
    return {
        "host": settings["clickhouse_host"],
        "port": settings["clickhouse_port"],
        "database": settings["clickhouse_database"],
        "username": settings["clickhouse_user"],
        "password": settings["clickhouse_password"],
        "ssl": False,
    }


def _find_or_create_destination(client, workspace_id, settings):
    if settings["destination_id"]:
        return settings["destination_id"]

    for row in client.list_destinations(workspace_id):
        if row.get("name") == DESTINATION_NAME:
            return row.get("destinationId") or row.get("destination_id")

    definition_id = _resolve_destination_definition_id(client, workspace_id)
    return client.create_destination(
        workspace_id=workspace_id,
        name=DESTINATION_NAME,
        definition_id=definition_id,
        configuration=_clickhouse_destination_configuration(settings),
    )


def _airbyte_source_configuration(extract_config, erp_type):
    if erp_type in D365_ERP_TYPES:
        return dict(extract_config)
    if erp_type in ERP_NEXT_ERP_TYPES:
        return dict(extract_config)
    raise AirbyteError(f"Unsupported erp_type '{erp_type}' for Airbyte provisioning.")


def _find_or_create_source(client, workspace_id, doc, extract_config, settings):
    source_name = _connector_source_name(doc.connector_name)
    configuration = _airbyte_source_configuration(extract_config, doc.erp_type)

    for row in client.list_sources(workspace_id):
        if row.get("name") == source_name:
            source_id = row.get("sourceId") or row.get("source_id")
            client.patch_source(
                source_id,
                name=source_name,
                workspace_id=workspace_id,
                configuration=configuration,
            )
            return source_id

    definition_id = _resolve_source_definition_id(
        client, workspace_id, doc.erp_type, settings
    )
    return client.create_source(
        workspace_id=workspace_id,
        name=source_name,
        definition_id=definition_id,
        configuration=configuration,
    )


def _find_or_create_connection(client, workspace_id, source_id, destination_id, connector_name):
    connection_name = _connection_name(connector_name)
    for row in client.list_connections(workspace_id):
        src = row.get("sourceId") or row.get("source_id")
        dst = row.get("destinationId") or row.get("destination_id")
        if src == source_id and dst == destination_id:
            return row.get("connectionId") or row.get("connection_id")

    return client.create_connection(
        source_id=source_id,
        destination_id=destination_id,
        name=connection_name,
    )


def test_connector_writeback(connector_name):
    """Validate write-back credentials for a Connector."""
    import frappe

    doc = frappe.get_doc("Connector", connector_name)
    writeback_config = build_writeback_config(doc)
    ok, message = check_writeback_connection(writeback_config, doc.erp_type)
    return {
        "ok": ok,
        "message": message,
        "connector_name": doc.connector_name,
    }


def test_connector_extract(connector_name):
    """Validate extract credentials for a Connector."""
    import frappe

    doc = frappe.get_doc("Connector", connector_name)
    extract_config = build_extract_config(doc)
    ok, message = check_extract_connection(extract_config, doc.erp_type)
    return {
        "ok": ok,
        "message": message,
        "connector_name": doc.connector_name,
    }


def provision_connector_airbyte(connector_name):
    """Test extract creds, then upsert Airbyte source + connection for a Connector."""
    import frappe

    doc = frappe.get_doc("Connector", connector_name)
    extract_config = build_extract_config(doc)
    if not extract_config:
        frappe.throw(
            "Extract credentials are incomplete. Configure the extract profile on "
            "the Connector before provisioning Airbyte."
        )

    ok, message = check_extract_connection(extract_config, doc.erp_type)
    if not ok:
        frappe.throw(message)

    settings = get_airbyte_settings()
    require_airbyte_settings(settings)

    client = AirbyteClient(
        settings["api_url"],
        settings["client_id"],
        settings["client_secret"],
    )
    client.authenticate()
    workspace_id = _resolve_workspace_id(client, settings)
    destination_id = _find_or_create_destination(client, workspace_id, settings)
    source_id = _find_or_create_source(
        client, workspace_id, doc, extract_config, settings
    )
    connection_id = _find_or_create_connection(
        client,
        workspace_id,
        source_id,
        destination_id,
        doc.connector_name,
    )

    doc.db_set("airbyte_source_id", source_id, update_modified=False)
    doc.db_set("airbyte_connection_id", connection_id, update_modified=False)
    frappe.db.commit()

    return {
        "ok": True,
        "message": message,
        "connector_name": doc.connector_name,
        "airbyte_source_id": source_id,
        "airbyte_connection_id": connection_id,
        "airbyte_workspace_id": workspace_id,
        "airbyte_destination_id": destination_id,
    }
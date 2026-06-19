"""Unit tests for D365 write-back config resolution."""
import os
import sys
import types
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import konsol.writeback_config as module

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _d365_connector(**overrides):
    base = {
        "name": "CONN-00001",
        "connector_name": "D365 F&O Production",
        "erp_type": "d365_fo",
        "enabled": 1,
        "writeback_enabled": 1,
        "writeback_credentials_separate": 0,
        "tenant_id": "tenant-1",
        "environment_url": "https://example.operations.dynamics.com",
        "extract_client_id": "extract-id",
        "writeback_client_id": "",
        "writeback_fiscal_year_start_month": 4,
        "host_url": "",
        "extract_api_key": "",
        "writeback_api_key": "",
        "legal_entities": [SimpleNamespace(entity_id="USMF", entity_name="US Mfg")],
        "get_password": lambda field, raise_exception=False: {
            "extract_client_secret": "extract-secret",
            "writeback_client_secret": "write-secret",
        }.get(field, ""),
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def test_resolve_prefers_connector_for_entity():
    connector_cfg = {
        "enabled": True,
        "resource_url": "https://example.operations.dynamics.com",
        "tenant_id": "tenant-1",
        "client_id": "extract-id",
        "client_secret": "extract-secret",
        "fiscal_year_start_month": 4,
        "source": "connector",
        "connector_name": "D365 F&O Production",
        "connector_id": "CONN-00001",
    }

    with patch.object(
        module, "_connector_writeback_config", return_value=connector_cfg
    ), patch.object(
        module, "_epm_settings_writeback_config", return_value={"enabled": True, "source": "epm_settings"}
    ):
        cfg = module.resolve_d365_writeback_config(entity_id="USMF")

    assert cfg["source"] == "connector"
    assert cfg["connector_name"] == "D365 F&O Production"
    assert cfg["client_id"] == "extract-id"
    assert cfg["resource_url"] == "https://example.operations.dynamics.com"
    assert cfg["fiscal_year_start_month"] == 4


def test_resolve_falls_back_to_epm_settings_when_no_connector():
    legacy = {
        "enabled": True,
        "resource_url": "https://legacy.operations.dynamics.com",
        "tenant_id": "legacy-tenant",
        "client_id": "legacy-client",
        "client_secret": "legacy-secret",
        "fiscal_year_start_month": 1,
        "source": "epm_settings",
    }
    with patch.object(module, "_connector_writeback_config", return_value=None), patch.object(
        module, "_epm_settings_writeback_config", return_value=legacy
    ):
        cfg = module.resolve_d365_writeback_config(entity_id="USMF")

    assert cfg["source"] == "epm_settings"
    assert cfg["client_id"] == "legacy-client"


def test_resolve_honours_disabled_connector_over_legacy():
    disabled_connector = {
        "enabled": False,
        "source": "connector",
        "connector_name": "D365 F&O Production",
        "resource_url": "https://example.operations.dynamics.com",
        "tenant_id": "tenant-1",
        "client_id": "id",
        "client_secret": "secret",
        "fiscal_year_start_month": 4,
    }
    with patch.object(
        module, "_connector_writeback_config", return_value=disabled_connector
    ), patch.object(
        module, "_epm_settings_writeback_config", return_value={"enabled": False, "source": "epm_settings"}
    ):
        cfg = module.resolve_d365_writeback_config(entity_id="USMF")

    assert cfg["enabled"] is False
    assert cfg["source"] == "connector"


def test_d365_writeback_get_config_delegates_to_resolver():
    src = open(os.path.join(APP_DIR, "d365_writeback.py")).read()
    assert "resolve_d365_writeback_config" in src
    assert "entity_id=doc.data_area_id" in src


def test_budget_input_resolves_config_by_entity():
    src = open(
        os.path.join(APP_DIR, "epm", "doctype", "budget_input", "budget_input.py")
    ).read()
    assert "get_config(entity_id=self.data_area_id)" in src
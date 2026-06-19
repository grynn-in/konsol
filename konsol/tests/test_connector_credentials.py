"""Unit tests for Connector extract / write-back credential profiles."""
from types import SimpleNamespace

import konsol.connector_credentials as cc


def _d365_doc(**overrides):
    base = {
        "name": "CONN-00001",
        "connector_name": "D365 F&O Production",
        "erp_type": "d365_fo",
        "tenant_id": "tenant-1",
        "environment_url": "https://example.operations.dynamics.com/",
        "extract_client_id": "extract-id",
        "extract_client_secret": "extract-secret",
        "extract_page_size": 250,
        "extract_cross_company": 1,
        "writeback_enabled": 0,
        "writeback_credentials_separate": 0,
        "writeback_client_id": "",
        "writeback_client_secret": "",
        "writeback_fiscal_year_start_month": 4,
        "get_password": lambda field, raise_exception=False: {
            "extract_client_secret": "extract-secret",
            "writeback_client_secret": "write-secret",
        }.get(field, ""),
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _erpnext_doc(**overrides):
    base = {
        "name": "CONN-00002",
        "connector_name": "ERPNext Production",
        "erp_type": "erpnext",
        "host_url": "https://erp.example.com/",
        "extract_api_key": "read-key",
        "extract_api_secret": "read-secret",
        "extract_page_size": 100,
        "writeback_enabled": 0,
        "writeback_credentials_separate": 0,
        "writeback_api_key": "",
        "writeback_api_secret": "",
        "get_password": lambda field, raise_exception=False: {
            "extract_api_secret": "read-secret",
            "writeback_api_secret": "write-secret",
        }.get(field, ""),
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def test_build_extract_config_d365():
    cfg = cc.build_extract_config(_d365_doc())
    assert cfg == {
        "tenant_id": "tenant-1",
        "client_id": "extract-id",
        "client_secret": "extract-secret",
        "environment_url": "https://example.operations.dynamics.com",
        "page_size": 250,
        "cross_company": True,
    }


def test_build_extract_config_erpnext():
    cfg = cc.build_extract_config(_erpnext_doc())
    assert cfg == {
        "host_url": "https://erp.example.com",
        "api_key": "read-key",
        "api_secret": "read-secret",
        "page_size": 100,
    }


def test_writeback_inherits_extract_credentials_by_default():
    doc = _d365_doc(writeback_enabled=1)
    assert cc.writeback_inherits_extract_credentials(doc) is True
    cfg = cc.build_writeback_config(doc)
    assert cfg["client_id"] == "extract-id"
    assert cfg["client_secret"] == "extract-secret"
    assert cfg["inherits_extract_credentials"] is True


def test_writeback_uses_separate_credentials_when_configured():
    doc = _d365_doc(
        writeback_enabled=1,
        writeback_credentials_separate=1,
        writeback_client_id="write-id",
    )
    assert cc.writeback_inherits_extract_credentials(doc) is False
    cfg = cc.build_writeback_config(doc)
    assert cfg["client_id"] == "write-id"
    assert cfg["client_secret"] == "write-secret"
    assert cfg["credentials_separate"] is True


def test_credentials_configured_flags():
    doc = _d365_doc(writeback_enabled=1)
    assert cc.credentials_configured(doc, "extract") is True
    assert cc.credentials_configured(doc, "writeback") is True

    incomplete = _d365_doc(extract_client_secret="")
    incomplete.get_password = lambda field, raise_exception=False: ""
    assert cc.credentials_configured(incomplete, "extract") is False


def test_connector_export_row_excludes_secrets():
    row = cc.connector_export_row(_d365_doc(writeback_enabled=1))
    assert "extract_client_secret" not in row
    assert "writeback_client_secret" not in row
    assert row["environment_url"] == "https://example.operations.dynamics.com/"
    assert row["extract_credentials_configured"] is True
    assert row["writeback_credentials_configured"] is True


def test_export_fields_never_include_password_fieldnames():
    assert cc.CONNECTOR_SECRET_FIELDS.isdisjoint(cc.CONNECTOR_EXPORTABLE_FIELDS)
"""Unit tests for ERP extract connectivity checks."""
from unittest.mock import MagicMock, patch

import konsol.extract_check as module


def test_check_extract_connection_requires_config():
    ok, message = module.check_extract_connection(None, "d365_fo")
    assert ok is False
    assert "incomplete" in message.lower()


@patch("konsol.extract_check.requests.post")
def test_check_d365_success(mock_post):
    mock_post.return_value = MagicMock(
        status_code=200,
        raise_for_status=MagicMock(),
        json=MagicMock(return_value={"access_token": "token"}),
    )
    ok, message = module.check_extract_connection(
        {
            "tenant_id": "tenant",
            "client_id": "client",
            "client_secret": "secret",
            "environment_url": "https://example.operations.dynamics.com",
        },
        "d365_fo",
    )
    assert ok is True
    assert "validated" in message.lower()


def test_check_writeback_d365_success():
    with patch("konsol.extract_check.requests.post") as mock_post:
        mock_post.return_value = MagicMock(
            status_code=200,
            raise_for_status=MagicMock(),
            json=MagicMock(return_value={"access_token": "token"}),
        )
        ok, message = module.check_writeback_connection(
            {
                "enabled": True,
                "tenant_id": "tenant",
                "client_id": "client",
                "client_secret": "secret",
                "resource_url": "https://example.operations.dynamics.com",
            },
            "d365_fo",
        )
    assert ok is True


@patch("konsol.extract_check.requests.get")
def test_check_erpnext_success(mock_get):
    mock_get.return_value = MagicMock(status_code=200, raise_for_status=MagicMock())
    ok, message = module.check_extract_connection(
        {
            "host_url": "https://erp.example.com",
            "api_key": "key",
            "api_secret": "secret",
        },
        "erpnext",
    )
    assert ok is True
    assert "validated" in message.lower()
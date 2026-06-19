"""Validate Connector extract credentials against the live ERP.

Mirrors the ``check`` command of the Airbyte custom source connectors without
requiring airbyte_cdk to be installed in the Frappe environment.
"""
from __future__ import annotations

import logging

import requests

from konsol.connector_credentials import D365_ERP_TYPES, ERP_NEXT_ERP_TYPES

logger = logging.getLogger(__name__)

D365_TOKEN_URL = "https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"


def check_writeback_connection(writeback_config, erp_type):
    """Return ``(ok, message)`` for a write-back profile."""
    if not writeback_config or not writeback_config.get("enabled"):
        return False, "Write-back is disabled on this connector."

    if erp_type in D365_ERP_TYPES:
        return _check_d365(
            {
                "tenant_id": writeback_config["tenant_id"],
                "client_id": writeback_config["client_id"],
                "client_secret": writeback_config["client_secret"],
                "environment_url": writeback_config["resource_url"],
            }
        )
    if erp_type in ERP_NEXT_ERP_TYPES:
        return _check_erpnext(
            {
                "host_url": writeback_config["host_url"],
                "api_key": writeback_config["api_key"],
                "api_secret": writeback_config["api_secret"],
            }
        )
    return False, f"Write-back connectivity check is not implemented for erp_type '{erp_type}'."


def check_extract_connection(extract_config, erp_type):
    """Return ``(ok, message)`` for an extract profile."""
    if not extract_config:
        return False, "Extract credentials are incomplete."

    if erp_type in D365_ERP_TYPES:
        return _check_d365(extract_config)
    if erp_type in ERP_NEXT_ERP_TYPES:
        return _check_erpnext(extract_config)
    return False, f"Extract connectivity check is not implemented for erp_type '{erp_type}'."


def _check_d365(config):
    tenant_id = config["tenant_id"]
    try:
        resp = requests.post(
            D365_TOKEN_URL.format(tenant_id=tenant_id),
            data={
                "grant_type": "client_credentials",
                "client_id": config["client_id"],
                "client_secret": config["client_secret"],
                "scope": config["environment_url"].rstrip("/") + "/.default",
            },
            timeout=30,
        )
        resp.raise_for_status()
        if "access_token" not in resp.json():
            return False, "Authentication failed: token response missing access_token."
        return True, "D365 extract credentials validated."
    except requests.exceptions.HTTPError:
        logger.debug("D365 extract check failed", exc_info=True)
        return False, (
            "D365 authentication failed. Check tenant_id, client_id, client_secret, "
            "and environment_url."
        )
    except requests.exceptions.RequestException:
        logger.debug("D365 extract check error", exc_info=True)
        return False, "D365 connection error. Check environment_url and network access."


def _check_erpnext(config):
    host_url = config["host_url"].rstrip("/")
    headers = {
        "Authorization": f"token {config['api_key']}:{config['api_secret']}",
    }
    url = f"{host_url}/api/method/frappe.auth.get_logged_user"
    try:
        resp = requests.get(url, headers=headers, timeout=30)
        resp.raise_for_status()
        return True, "ERPNext extract credentials validated."
    except requests.exceptions.HTTPError:
        logger.debug("ERPNext extract check failed", exc_info=True)
        return False, (
            "ERPNext authentication failed. Check host_url, api_key, and api_secret."
        )
    except requests.exceptions.RequestException:
        logger.debug("ERPNext extract check error", exc_info=True)
        return False, "ERPNext connection error. Check host_url and network access."
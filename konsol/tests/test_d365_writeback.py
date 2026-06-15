"""Unit tests for D365 budget write-back (Phase 3).

Pure mapping/auth/client logic — no live Frappe site. ``frappe`` is imported
lazily inside the module's site-bound functions, so importing the module and
exercising build_entries / get_token / post_entries / error_message needs only
``requests`` (mocked here).
"""
import json
import os
import types
from unittest.mock import MagicMock, patch

import requests

from konsol import d365_writeback as wb

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _doc(**over):
    """Minimal Budget Input stand-in (attribute + .get access)."""
    data = {
        "name": "BUD-0001",
        "data_area_id": "USMF",
        "fiscal_year": 2024,
        "main_account": "401100",
        "dim_cost_center": "CC001",
        "dim_department": "",
        "periods": [
            types.SimpleNamespace(fiscal_period=1, amount=100.0),
            types.SimpleNamespace(fiscal_period=2, amount=0.0),    # skipped
            types.SimpleNamespace(fiscal_period=3, amount=250.5),
        ],
    }
    data.update(over)
    ns = types.SimpleNamespace(**data)
    ns.get = lambda k, d=None: getattr(ns, k, d)
    return ns


# --- idempotency + period mapping ---

def test_budget_model_id_is_deterministic():
    assert wb.budget_model_id("BUD-0001") == "EPM-BUD-0001"


def test_period_first_day():
    assert wb._period_first_day(2024, 1) == "2024-01-01"
    assert wb._period_first_day(2024, 12) == "2024-12-01"
    assert wb._period_first_day(2024, 0) == "2024-01-01"   # clamped


def test_dimension_values_skips_empty():
    vals = wb._dimension_values(_doc())
    assert vals == {"CostCenter": "CC001"}            # department empty -> omitted


def test_build_entries_maps_and_skips_zero():
    entries = wb.build_entries(_doc())
    assert len(entries) == 2                            # zero-amount period dropped
    e = entries[0]
    assert e["BudgetModelId"] == "EPM-BUD-0001"        # idempotency tag
    assert e["LegalEntityId"] == "USMF"
    assert e["AccountingDate"] == "2024-01-01"
    assert e["MainAccountId"] == "401100"
    assert e["AccountingCurrencyAmount"] == 100.0
    assert e["LedgerDimensionValues"] == {"CostCenter": "CC001"}
    assert entries[1]["AccountingCurrencyAmount"] == 250.5


# --- OAuth + POST client (requests mocked) ---

def test_get_token_uses_client_credentials():
    cfg = {"tenant_id": "tid", "client_id": "cid", "client_secret": "sec",
           "resource_url": "https://org.operations.dynamics.com"}
    with patch.object(wb.requests, "post") as post:
        post.return_value = MagicMock(status_code=200, **{"json.return_value": {"access_token": "TOK"}})
        post.return_value.raise_for_status = lambda: None
        token = wb.get_token(cfg)
    assert token == "TOK"
    _, kwargs = post.call_args
    assert kwargs["data"]["grant_type"] == "client_credentials"
    assert kwargs["data"]["scope"] == "https://org.operations.dynamics.com/.default"
    assert post.call_args[0][0].endswith("/tid/oauth2/v2.0/token")


def test_post_entries_targets_budget_register_entries_with_bearer():
    cfg = {"resource_url": "https://org.operations.dynamics.com"}
    with patch.object(wb.requests, "post") as post:
        resp = MagicMock(content=b"{}")
        resp.json.return_value = {}
        resp.raise_for_status = lambda: None
        post.return_value = resp
        wb.post_entries(cfg, "TOK", [{"BudgetModelId": "EPM-BUD-0001"}])
    url = post.call_args[0][0]
    assert url == "https://org.operations.dynamics.com/data/BudgetRegisterEntries"
    assert post.call_args[1]["headers"]["Authorization"] == "Bearer TOK"


# --- error handling (non-sensitive) ---

def test_error_message_is_generic_for_http_error():
    resp = MagicMock(status_code=400)
    err = requests.exceptions.HTTPError(response=resp)
    msg = wb.error_message(err)
    assert "HTTP 400" in msg and "secret" not in msg.lower()


def test_error_message_for_connection_error():
    assert "Connection error" in wb.error_message(requests.exceptions.ConnectionError())


# --- config contract ---

def test_required_config_keys():
    assert set(wb._REQUIRED) == {"resource_url", "tenant_id", "client_id", "client_secret"}


def test_epm_settings_has_d365_fields():
    meta = json.load(open(os.path.join(APP_DIR, "pipeline", "doctype", "epm_settings", "epm_settings.json")))
    names = {f["fieldname"] for f in meta["fields"]}
    for f in ["enable_d365_budget_writeback", "d365_resource_url", "d365_tenant_id",
              "d365_client_id", "d365_client_secret"]:
        assert f in names, "Missing EPM Settings field: " + f
    secret = next(f for f in meta["fields"] if f["fieldname"] == "d365_client_secret")
    assert secret["fieldtype"] == "Password"


def test_budget_input_has_writeback_status_fields():
    meta = json.load(open(os.path.join(APP_DIR, "epm", "doctype", "budget_input", "budget_input.json")))
    names = {f["fieldname"] for f in meta["fields"]}
    assert "d365_writeback_status" in names and "d365_writeback_error" in names

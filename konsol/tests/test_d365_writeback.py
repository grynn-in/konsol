"""Unit tests for D365 budget write-back (post-reshape: Budget Sheet grain).

Pure mapping/auth/client logic — no live Frappe site. ``frappe`` is imported
lazily inside the module's site-bound functions, so importing the module and
exercising build_entries / get_token / post_entries / error_message needs only
``requests`` (mocked here). ``PERIOD_FIELDS`` lives in the frappe-free
``konsol.epm.budget_periods`` so ``build_entries`` stays importable without a site.
"""
import json
import os
import types
from unittest.mock import MagicMock, patch

import requests

from konsol import d365_writeback as wb

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _ns(**data):
    ns = types.SimpleNamespace(**data)
    ns.get = lambda k, d=None: getattr(ns, k, d)
    return ns


def _line(**over):
    """Minimal Budget Line stand-in: account + dims + 12 wide month columns."""
    data = {"main_account": "401100", "dim_cost_center": "CC001", "dim_department": ""}
    for n in range(1, 13):
        data["period_%02d" % n] = 0.0
    data["period_01"] = 100.0
    data["period_03"] = 250.5
    data.update(over)
    return _ns(**data)


def _sheet(**over):
    """Minimal Budget Sheet stand-in (attribute + .get access, .lines list)."""
    lines = over.pop("lines", None)
    if lines is None:
        lines = [_line()]
    data = {"name": "BSHT-0001", "data_area_id": "USMF", "lines": lines}
    data.update(over)
    return _ns(**data)


# --- idempotency + period mapping ---

def test_budget_model_id_is_deterministic():
    assert wb.budget_model_id("BSHT-0001") == "EPM-BSHT-0001"


def test_period_first_day():
    assert wb._period_first_day(2024, 1) == "2024-01-01"
    assert wb._period_first_day(2024, 12) == "2024-12-01"
    assert wb._period_first_day(2024, 0) == "2024-01-01"   # clamped


def test_dimension_values_skips_empty():
    vals = wb._dimension_values(_line())
    assert vals == {"CostCenter": "CC001"}            # department empty -> omitted


def test_build_entries_explodes_wide_to_tall_and_skips_zero():
    entries = wb.build_entries(_sheet(), 2024)
    assert len(entries) == 2                            # only 2 non-zero months
    e = entries[0]
    assert e["BudgetModelId"] == "EPM-BSHT-0001"        # idempotency tag = sheet grain
    assert e["LegalEntityId"] == "USMF"
    assert e["AccountingDate"] == "2024-01-01"          # period_01
    assert e["MainAccountId"] == "401100"
    assert e["AccountingCurrencyAmount"] == 100.0
    assert e["LedgerDimensionValues"] == {"CostCenter": "CC001"}
    assert entries[1]["AccountingCurrencyAmount"] == 250.5
    assert entries[1]["AccountingDate"] == "2024-03-01"  # period_03


def test_build_entries_spans_multiple_lines():
    sheet = _sheet(lines=[
        _line(main_account="401100"),
        _line(main_account="500200", dim_cost_center="CC002"),
    ])
    entries = wb.build_entries(sheet, 2024)
    assert len(entries) == 4                            # 2 non-zero months x 2 lines
    assert {e["MainAccountId"] for e in entries} == {"401100", "500200"}


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
        wb.post_entries(cfg, "TOK", [{"BudgetModelId": "EPM-BSHT-0001"}])
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


def test_budget_sheet_has_writeback_status_fields():
    meta = json.load(open(os.path.join(APP_DIR, "epm", "doctype", "budget_sheet", "budget_sheet.json")))
    names = {f["fieldname"] for f in meta["fields"]}
    assert "d365_writeback_status" in names and "d365_writeback_error" in names

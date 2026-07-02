"""Unit tests for D365 write-back go-live features (Phase 3 follow-up, issue #28).

Covers:
  - FiscalCalendar abstraction (StandardFiscalCalendar + CustomFiscalCalendar)
  - get_fiscal_calendar config integration
  - build_entries with non-default calendars
  - fetch_existing_entries (GET pre-flight)
  - build_batch_body structure
  - push_replace_batch ($batch POST)
  - enqueue_push_budget_sheet (cycle-lock wiring helper)

No live Frappe site or D365 tenant required. ``frappe`` and ``requests`` are
mocked the same way as ``test_d365_writeback_orchestration.py``.

NEEDS-LIVE-TENANT — not tested here:
  - Actual OData field names (BudgetModelId, RecId, dataAreaId, …)
  - LedgerDimensionValues attribute names vs. the tenant's dimension config
  - $batch endpoint availability and changeset atomicity
  - The real fiscal period start dates from D365 Fiscal calendars
  - HTTP response parsing for $batch multipart replies
"""
import json
import sys
import types
from unittest.mock import MagicMock, call, patch

import pytest
import requests

from konsol import d365_writeback as wb


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _doc(**over):
    """Minimal Budget Sheet stand-in compatible with build_entries.

    One wide line with non-zero period_01 (=100) and period_03 (=250); fiscal
    year is passed to build_entries separately (it lives on the parent cycle).
    """
    line = types.SimpleNamespace(
        main_account="401100", dim_cost_center="CC001", dim_department="")
    for n in range(1, 13):
        setattr(line, "period_%02d" % n, 0.0)
    line.period_01 = 100.0
    line.period_03 = 250.0
    line.get = lambda k, d=None: getattr(line, k, d)

    data = {"name": "BSHT-0001", "data_area_id": "USMF", "lines": [line]}
    data.update(over)
    ns = types.SimpleNamespace(**data)
    ns.get = lambda k, d=None: getattr(ns, k, d)
    return ns


def _install_fake_frappe(monkeypatch):
    fr = types.ModuleType("frappe")
    enqueued = {}
    fr.enqueue = lambda method, queue="default", **kw: enqueued.update(
        method=method, queue=queue, kw=kw
    )
    monkeypatch.setitem(sys.modules, "frappe", fr)
    return fr, enqueued


# ---------------------------------------------------------------------------
# StandardFiscalCalendar
# ---------------------------------------------------------------------------

class TestStandardFiscalCalendar:
    def test_jan_start_period_1(self):
        cal = wb.StandardFiscalCalendar(start_month=1)
        assert cal.period_first_day(2024, 1) == "2024-01-01"

    def test_jan_start_period_12(self):
        cal = wb.StandardFiscalCalendar(start_month=1)
        assert cal.period_first_day(2024, 12) == "2024-12-01"

    def test_apr_start_period_1_maps_to_apr(self):
        cal = wb.StandardFiscalCalendar(start_month=4)
        assert cal.period_first_day(2024, 1) == "2024-04-01"

    def test_apr_start_period_9_maps_to_dec(self):
        cal = wb.StandardFiscalCalendar(start_month=4)
        assert cal.period_first_day(2024, 9) == "2024-12-01"

    def test_apr_start_period_10_rolls_to_next_calendar_year(self):
        cal = wb.StandardFiscalCalendar(start_month=4)
        assert cal.period_first_day(2024, 10) == "2025-01-01"

    def test_apr_start_period_12_maps_to_mar_next_year(self):
        cal = wb.StandardFiscalCalendar(start_month=4)
        assert cal.period_first_day(2024, 12) == "2025-03-01"

    def test_jul_start_period_6_maps_to_dec(self):
        cal = wb.StandardFiscalCalendar(start_month=7)
        assert cal.period_first_day(2024, 6) == "2024-12-01"

    def test_jul_start_period_7_rolls_to_jan_next_year(self):
        cal = wb.StandardFiscalCalendar(start_month=7)
        assert cal.period_first_day(2024, 7) == "2025-01-01"

    def test_clamps_period_zero_to_one(self):
        cal = wb.StandardFiscalCalendar(start_month=1)
        assert cal.period_first_day(2024, 0) == "2024-01-01"

    def test_clamps_period_above_12(self):
        cal = wb.StandardFiscalCalendar(start_month=1)
        assert cal.period_first_day(2024, 13) == "2024-12-01"

    def test_invalid_start_month_zero_raises(self):
        with pytest.raises(ValueError):
            wb.StandardFiscalCalendar(start_month=0)

    def test_invalid_start_month_13_raises(self):
        with pytest.raises(ValueError):
            wb.StandardFiscalCalendar(start_month=13)

    def test_jan_start_matches_legacy_period_first_day(self):
        cal = wb.StandardFiscalCalendar(start_month=1)
        for period in range(1, 13):
            assert cal.period_first_day(2024, period) == wb._period_first_day(2024, period)


# ---------------------------------------------------------------------------
# CustomFiscalCalendar
# ---------------------------------------------------------------------------

class TestCustomFiscalCalendar:
    SAMPLE_MAP = {1: (0, 4, 6), 2: (0, 5, 4), 3: (0, 6, 1)}

    def test_maps_known_period(self):
        cal = wb.CustomFiscalCalendar(self.SAMPLE_MAP)
        assert cal.period_first_day(2024, 1) == "2024-04-06"

    def test_maps_second_period(self):
        cal = wb.CustomFiscalCalendar(self.SAMPLE_MAP)
        assert cal.period_first_day(2024, 2) == "2024-05-04"

    def test_maps_period_with_positive_year_offset(self):
        period_map = {1: (1, 1, 1)}
        cal = wb.CustomFiscalCalendar(period_map)
        assert cal.period_first_day(2024, 1) == "2025-01-01"

    def test_maps_period_with_zero_year_offset(self):
        period_map = {1: (0, 4, 1)}
        cal = wb.CustomFiscalCalendar(period_map)
        assert cal.period_first_day(2024, 1) == "2024-04-01"

    def test_unknown_period_raises_value_error(self):
        cal = wb.CustomFiscalCalendar({1: (0, 1, 1)})
        with pytest.raises(ValueError, match="Period 5"):
            cal.period_first_day(2024, 5)

    def test_accepts_string_keys_in_map(self):
        cal = wb.CustomFiscalCalendar({"1": (0, 4, 1)})
        assert cal.period_first_day(2024, 1) == "2024-04-01"

    def test_formats_day_with_zero_padding(self):
        cal = wb.CustomFiscalCalendar({1: (0, 1, 6)})
        assert cal.period_first_day(2024, 1) == "2024-01-06"


# ---------------------------------------------------------------------------
# get_fiscal_calendar
# ---------------------------------------------------------------------------

def test_get_fiscal_calendar_default_is_jan():
    cal = wb.get_fiscal_calendar({})
    assert isinstance(cal, wb.StandardFiscalCalendar)
    assert cal.start_month == 1


def test_get_fiscal_calendar_reads_start_month_from_cfg():
    cal = wb.get_fiscal_calendar({"fiscal_year_start_month": 4})
    assert isinstance(cal, wb.StandardFiscalCalendar)
    assert cal.start_month == 4


def test_get_fiscal_calendar_none_value_defaults_to_jan():
    cal = wb.get_fiscal_calendar({"fiscal_year_start_month": None})
    assert cal.start_month == 1


def test_get_fiscal_calendar_missing_key_defaults_to_jan():
    cal = wb.get_fiscal_calendar({"enabled": True})
    assert cal.start_month == 1


# ---------------------------------------------------------------------------
# build_entries with non-default calendars
# ---------------------------------------------------------------------------

def test_build_entries_default_calendar_is_jan():
    entries = wb.build_entries(_doc(), 2024)
    assert entries[0]["AccountingDate"] == "2024-01-01"
    assert entries[1]["AccountingDate"] == "2024-03-01"


def test_build_entries_with_apr_standard_calendar():
    cal = wb.StandardFiscalCalendar(start_month=4)
    entries = wb.build_entries(_doc(), 2024, fiscal_calendar=cal)
    assert entries[0]["AccountingDate"] == "2024-04-01"
    assert entries[1]["AccountingDate"] == "2024-06-01"


def test_build_entries_with_custom_calendar():
    cal = wb.CustomFiscalCalendar({1: (0, 4, 6), 3: (0, 6, 1)})
    entries = wb.build_entries(_doc(), 2024, fiscal_calendar=cal)
    assert entries[0]["AccountingDate"] == "2024-04-06"
    assert entries[1]["AccountingDate"] == "2024-06-01"


def test_build_entries_explicit_none_calendar_uses_default():
    entries = wb.build_entries(_doc(), 2024, fiscal_calendar=None)
    assert entries[0]["AccountingDate"] == "2024-01-01"


# ---------------------------------------------------------------------------
# fetch_existing_entries
# ---------------------------------------------------------------------------

def test_fetch_existing_entries_calls_get_with_odata_filter():
    cfg = {"resource_url": "https://org.dynamics.com"}
    with patch.object(wb.requests, "get") as get_mock:
        get_mock.return_value = MagicMock(
            **{"json.return_value": {"value": [{"dataAreaId": "USMF", "RecId": 42}]}},
        )
        get_mock.return_value.raise_for_status = lambda: None
        result = wb.fetch_existing_entries(cfg, "TOK", "EPM-BUD-0001")

    url = get_mock.call_args[0][0]
    assert "BudgetRegisterEntries" in url
    assert "BudgetModelId" in url
    assert "EPM-BUD-0001" in url


def test_fetch_existing_entries_sends_bearer_token():
    cfg = {"resource_url": "https://org.dynamics.com"}
    with patch.object(wb.requests, "get") as get_mock:
        get_mock.return_value = MagicMock(**{"json.return_value": {"value": []}})
        get_mock.return_value.raise_for_status = lambda: None
        wb.fetch_existing_entries(cfg, "MY-TOK", "EPM-BUD-0001")

    headers = get_mock.call_args[1]["headers"]
    assert headers["Authorization"] == "Bearer MY-TOK"


def test_fetch_existing_entries_returns_value_list():
    cfg = {"resource_url": "https://org.dynamics.com"}
    records = [{"dataAreaId": "USMF", "RecId": 1}, {"dataAreaId": "USMF", "RecId": 2}]
    with patch.object(wb.requests, "get") as get_mock:
        get_mock.return_value = MagicMock(**{"json.return_value": {"value": records}})
        get_mock.return_value.raise_for_status = lambda: None
        result = wb.fetch_existing_entries(cfg, "TOK", "EPM-BUD-0001")

    assert result == records


def test_fetch_existing_entries_returns_empty_when_none_exist():
    cfg = {"resource_url": "https://org.dynamics.com"}
    with patch.object(wb.requests, "get") as get_mock:
        get_mock.return_value = MagicMock(**{"json.return_value": {"value": []}})
        get_mock.return_value.raise_for_status = lambda: None
        result = wb.fetch_existing_entries(cfg, "TOK", "EPM-BUD-0001")

    assert result == []


def test_fetch_existing_entries_escapes_single_quotes_in_model_id():
    cfg = {"resource_url": "https://org.dynamics.com"}
    with patch.object(wb.requests, "get") as get_mock:
        get_mock.return_value = MagicMock(**{"json.return_value": {"value": []}})
        get_mock.return_value.raise_for_status = lambda: None
        wb.fetch_existing_entries(cfg, "TOK", "EPM-O'Reilly")

    url = get_mock.call_args[0][0]
    assert "O''Reilly" in url


# ---------------------------------------------------------------------------
# build_batch_body
# ---------------------------------------------------------------------------

def test_build_batch_body_contains_batch_boundary():
    body = wb.build_batch_body("mybatch", "mychangeset", [], [])
    assert "--mybatch" in body


def test_build_batch_body_contains_changeset_boundary():
    body = wb.build_batch_body("mybatch", "mychangeset", [], [])
    assert "--mychangeset" in body


def test_build_batch_body_delete_operation_present():
    delete_keys = ["dataAreaId='USMF',RecId=42"]
    body = wb.build_batch_body("b1", "c1", delete_keys, [])
    assert "DELETE /data/BudgetRegisterEntries(dataAreaId='USMF',RecId=42) HTTP/1.1" in body


def test_build_batch_body_delete_includes_if_match():
    delete_keys = ["dataAreaId='USMF',RecId=42"]
    body = wb.build_batch_body("b1", "c1", delete_keys, [])
    assert "If-Match: *" in body


def test_build_batch_body_post_operation_present():
    entries = [{"BudgetModelId": "EPM-BUD-0001", "LegalEntityId": "USMF"}]
    body = wb.build_batch_body("b1", "c1", [], entries)
    assert "POST /data/BudgetRegisterEntries HTTP/1.1" in body


def test_build_batch_body_post_includes_json_payload():
    entries = [{"BudgetModelId": "EPM-BUD-0001"}]
    body = wb.build_batch_body("b1", "c1", [], entries)
    assert "EPM-BUD-0001" in body


def test_build_batch_body_delete_before_post():
    delete_keys = ["dataAreaId='USMF',RecId=1"]
    entries = [{"BudgetModelId": "EPM-BUD-0001"}]
    body = wb.build_batch_body("b1", "c1", delete_keys, entries)
    assert "DELETE" in body and "POST" in body
    assert body.index("DELETE") < body.index("POST")


def test_build_batch_body_no_deletes_has_only_post():
    entries = [{"BudgetModelId": "EPM-BUD-0001"}]
    body = wb.build_batch_body("b1", "c1", [], entries)
    assert "DELETE" not in body
    assert "POST" in body


def test_build_batch_body_no_entries_has_only_delete():
    delete_keys = ["dataAreaId='USMF',RecId=1"]
    body = wb.build_batch_body("b1", "c1", delete_keys, [])
    assert "DELETE" in body
    assert "POST" not in body


def test_build_batch_body_multiple_entries():
    entries = [
        {"BudgetModelId": "EPM-BUD-0001", "Amount": 100},
        {"BudgetModelId": "EPM-BUD-0001", "Amount": 200},
    ]
    body = wb.build_batch_body("b1", "c1", [], entries)
    assert body.count("POST /data/BudgetRegisterEntries") == 2


# ---------------------------------------------------------------------------
# push_replace_batch
# ---------------------------------------------------------------------------

def test_push_replace_batch_posts_to_batch_endpoint():
    cfg = {"resource_url": "https://org.dynamics.com"}
    with patch.object(wb.requests, "get") as get_mock, \
         patch.object(wb.requests, "post") as post_mock:
        get_mock.return_value = MagicMock(**{"json.return_value": {"value": []}})
        get_mock.return_value.raise_for_status = lambda: None
        post_mock.return_value = MagicMock(status_code=200)
        post_mock.return_value.raise_for_status = lambda: None

        wb.push_replace_batch(cfg, "TOK", "EPM-BUD-0001", [{"BudgetModelId": "EPM-BUD-0001"}])

    url = post_mock.call_args[0][0]
    assert url == "https://org.dynamics.com/data/$batch"


def test_push_replace_batch_sends_multipart_content_type():
    cfg = {"resource_url": "https://org.dynamics.com"}
    with patch.object(wb.requests, "get") as get_mock, \
         patch.object(wb.requests, "post") as post_mock:
        get_mock.return_value = MagicMock(**{"json.return_value": {"value": []}})
        get_mock.return_value.raise_for_status = lambda: None
        post_mock.return_value = MagicMock(status_code=200)
        post_mock.return_value.raise_for_status = lambda: None

        wb.push_replace_batch(cfg, "TOK", "EPM-BUD-0001", [])

    ct = post_mock.call_args[1]["headers"]["Content-Type"]
    assert ct.startswith("multipart/mixed; boundary=")


def test_push_replace_batch_deletes_existing_then_inserts():
    cfg = {"resource_url": "https://org.dynamics.com"}
    existing = [{"dataAreaId": "USMF", "RecId": 99}]
    with patch.object(wb.requests, "get") as get_mock, \
         patch.object(wb.requests, "post") as post_mock:
        get_mock.return_value = MagicMock(**{"json.return_value": {"value": existing}})
        get_mock.return_value.raise_for_status = lambda: None
        post_mock.return_value = MagicMock(status_code=200)
        post_mock.return_value.raise_for_status = lambda: None

        wb.push_replace_batch(cfg, "TOK", "EPM-BUD-0001", [{"BudgetModelId": "EPM-BUD-0001"}])

    batch_body = post_mock.call_args[1]["data"].decode("utf-8")
    assert "DELETE" in batch_body
    assert "RecId=99" in batch_body
    assert "POST" in batch_body


def test_push_replace_batch_sends_bearer_auth():
    cfg = {"resource_url": "https://org.dynamics.com"}
    with patch.object(wb.requests, "get") as get_mock, \
         patch.object(wb.requests, "post") as post_mock:
        get_mock.return_value = MagicMock(**{"json.return_value": {"value": []}})
        get_mock.return_value.raise_for_status = lambda: None
        post_mock.return_value = MagicMock(status_code=200)
        post_mock.return_value.raise_for_status = lambda: None

        wb.push_replace_batch(cfg, "MY-TOKEN", "EPM-BUD-0001", [])

    headers = post_mock.call_args[1]["headers"]
    assert headers["Authorization"] == "Bearer MY-TOKEN"


def test_push_replace_batch_calls_get_for_preflight():
    cfg = {"resource_url": "https://org.dynamics.com"}
    with patch.object(wb.requests, "get") as get_mock, \
         patch.object(wb.requests, "post") as post_mock:
        get_mock.return_value = MagicMock(**{"json.return_value": {"value": []}})
        get_mock.return_value.raise_for_status = lambda: None
        post_mock.return_value = MagicMock(status_code=200)
        post_mock.return_value.raise_for_status = lambda: None

        wb.push_replace_batch(cfg, "TOK", "EPM-BUD-0001", [])

    get_mock.assert_called_once()
    get_url = get_mock.call_args[0][0]
    assert "BudgetRegisterEntries" in get_url


def test_push_replace_batch_no_existing_sends_only_posts():
    cfg = {"resource_url": "https://org.dynamics.com"}
    entries = [{"BudgetModelId": "EPM-BUD-0001"}]
    with patch.object(wb.requests, "get") as get_mock, \
         patch.object(wb.requests, "post") as post_mock:
        get_mock.return_value = MagicMock(**{"json.return_value": {"value": []}})
        get_mock.return_value.raise_for_status = lambda: None
        post_mock.return_value = MagicMock(status_code=200)
        post_mock.return_value.raise_for_status = lambda: None

        wb.push_replace_batch(cfg, "TOK", "EPM-BUD-0001", entries)

    batch_body = post_mock.call_args[1]["data"].decode("utf-8")
    assert "DELETE" not in batch_body
    assert "POST" in batch_body


# ---------------------------------------------------------------------------
# enqueue_push_budget_sheet (cycle-lock wiring)
# ---------------------------------------------------------------------------

def test_enqueue_push_budget_sheet_calls_frappe_enqueue(monkeypatch):
    _fr, enqueued = _install_fake_frappe(monkeypatch)
    wb.enqueue_push_budget_sheet("BSHT-0001")
    assert enqueued["method"] == "konsol.d365_writeback.push_budget_sheet"
    assert enqueued["kw"]["name"] == "BSHT-0001"


def test_enqueue_push_budget_sheet_uses_long_queue(monkeypatch):
    _fr, enqueued = _install_fake_frappe(monkeypatch)
    wb.enqueue_push_budget_sheet("BSHT-0001")
    assert enqueued.get("queue") == "long"


def test_enqueue_push_budget_sheet_propagates_name(monkeypatch):
    _fr, enqueued = _install_fake_frappe(monkeypatch)
    wb.enqueue_push_budget_sheet("BSHT-CUSTOM-99")
    assert enqueued["kw"]["name"] == "BSHT-CUSTOM-99"


# ---------------------------------------------------------------------------
# Review fixes: nextLink paging, trailing CRLF, changeset error detection
# ---------------------------------------------------------------------------

def test_fetch_existing_entries_follows_nextlink_paging():
    # >1 page must be fully paged, else the DELETE set is incomplete and the
    # budget silently duplicates after the POST.
    cfg = {"resource_url": "https://org.dynamics.com"}
    page1 = {"value": [{"dataAreaId": "USMF", "RecId": 1}],
             "@odata.nextLink": "https://org.dynamics.com/data/BudgetRegisterEntries?$skiptoken=x"}
    page2 = {"value": [{"dataAreaId": "USMF", "RecId": 2}]}
    with patch.object(wb.requests, "get") as get_mock:
        r1 = MagicMock(**{"json.return_value": page1}); r1.raise_for_status = lambda: None
        r2 = MagicMock(**{"json.return_value": page2}); r2.raise_for_status = lambda: None
        get_mock.side_effect = [r1, r2]
        result = wb.fetch_existing_entries(cfg, "TOK", "EPM-BUD-0001")
    assert get_mock.call_count == 2                      # followed the nextLink
    assert [e["RecId"] for e in result] == [1, 2]        # both pages collected
    assert get_mock.call_args_list[1][0][0] == page1["@odata.nextLink"]


def test_build_batch_body_ends_with_trailing_crlf():
    body = wb.build_batch_body("b1", "c1", [], [{"BudgetModelId": "EPM-X"}])
    assert body.endswith("--b1--\r\n")                   # RFC 2046 close-delimiter CRLF


def test_raise_on_changeset_errors_passes_on_all_2xx():
    resp = MagicMock()
    resp.text = (
        "--b\r\nContent-Type: application/http\r\n\r\nHTTP/1.1 204 No Content\r\n"
        "--b\r\nContent-Type: application/http\r\n\r\nHTTP/1.1 201 Created\r\n--b--\r\n"
    )
    wb._raise_on_changeset_errors(resp)                  # no raise


def test_raise_on_changeset_errors_raises_on_embedded_failure():
    resp = MagicMock()
    resp.text = (
        "--b\r\n\r\nHTTP/1.1 204 No Content\r\n"
        "--b\r\n\r\nHTTP/1.1 400 Bad Request\r\n--b--\r\n"   # embedded op failed
    )
    with pytest.raises(requests.exceptions.HTTPError) as ei:
        wb._raise_on_changeset_errors(resp)
    assert "400" in str(ei.value)


def test_push_replace_batch_surfaces_embedded_changeset_failure():
    cfg = {"resource_url": "https://org.dynamics.com"}
    with patch.object(wb.requests, "get") as get_mock, \
         patch.object(wb.requests, "post") as post_mock:
        get_mock.return_value = MagicMock(**{"json.return_value": {"value": []}})
        get_mock.return_value.raise_for_status = lambda: None
        # envelope is 200 but a POST inside the changeset failed
        post_mock.return_value = MagicMock(
            status_code=200,
            text="--b\r\n\r\nHTTP/1.1 500 Internal Server Error\r\n--b--\r\n",
        )
        post_mock.return_value.raise_for_status = lambda: None
        with pytest.raises(requests.exceptions.HTTPError):
            wb.push_replace_batch(cfg, "TOK", "EPM-BUD-0001", [{"BudgetModelId": "EPM-BUD-0001"}])


def test_batch_entity_key_missing_field_raises_clean_error():
    with pytest.raises(KeyError) as ei:
        wb._batch_entity_key({"dataAreaId": "USMF"})     # RecId missing
    assert "metadata" in str(ei.value).lower()

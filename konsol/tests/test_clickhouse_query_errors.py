"""A failed ClickHouse query says why (konsol#214, PR #217 review 1).

api._clickhouse_query used to raise a bare HTTP error and the batch reader
turned it into a generic "ClickHouse query failed", so an unknown column (for
example budget_scenario_id before the warehouse change is built) was
invisible. Now ClickHouse's response body is logged and its first line is in
the message the caller shows. api.py is loaded under a private name with a
stub frappe and a fake requests.get that answers HTTP 400.
"""
import importlib.util
import os
import sys
import types

import requests

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_FIRST_LINE = (
    "Code: 47. DB::Exception: Unknown expression identifier "
    "'budget_scenario_id' in scope SELECT data_area_id. (UNKNOWN_IDENTIFIER)"
)
_BODY = _FIRST_LINE + "\n" + "\n".join(f"{i}. DB::frame_{i}" for i in range(400))


class _Resp:
    def __init__(self, status_code, text):
        self.status_code = status_code
        self.text = text
        self.ok = status_code < 400

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.exceptions.HTTPError(
                f"{self.status_code} Client Error: Bad Request", response=self)


def _load_api(logged):
    """api.py with a stub frappe whose log_error records (title, message)."""
    def log_error(title=None, message=None, **kw):
        logged.append((title, message))

    fake_frappe = types.ModuleType("frappe")
    fake_frappe.get_all = lambda *a, **k: []
    fake_frappe.whitelist = lambda *a, **k: (lambda fn: fn)
    fake_frappe.log_error = log_error
    fake_frappe.get_traceback = lambda: "Traceback: HTTPError"
    fake_utils = types.ModuleType("frappe.utils")
    fake_utils.now_datetime = lambda: None
    fake_frappe.utils = fake_utils
    stubs = {
        "frappe": fake_frappe,
        "frappe.utils": fake_utils,
        "konsol.clickhouse": types.SimpleNamespace(
            connection_url=lambda s: "http://zz-clickhouse:8123/",
            get_connection=lambda: {}),
    }
    before = set(sys.modules)
    saved = {k: sys.modules.get(k) for k in stubs}
    sys.modules.update(stubs)
    try:
        spec = importlib.util.spec_from_file_location(
            "_host_api_k214_errors", os.path.join(APP_DIR, "api.py"))
        api = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(api)
    finally:
        for key in set(sys.modules) - before:
            if key.split(".")[0] in ("frappe", "konsol"):
                del sys.modules[key]
        for k, mod in saved.items():
            if mod is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = mod
    # api.requests is the real module; give api its own copy with a fake get
    fake_requests = types.SimpleNamespace(
        get=lambda *a, **k: _Resp(400, _BODY),
        exceptions=requests.exceptions,
    )
    api.requests = fake_requests
    return api


_SETTINGS = {"user": "zz", "password": "zz"}


def test_failed_query_message_carries_clickhouse_first_line():
    logged = []
    api = _load_api(logged)
    try:
        api._clickhouse_query("SELECT 1", {}, _SETTINGS)
    except Exception as exc:  # noqa: BLE001 — the message is what matters
        message = str(exc)
    else:
        raise AssertionError("a 400 response must raise")
    assert _FIRST_LINE in message
    assert "DB::frame_0" not in message  # first line only
    assert len(message) <= 300 + len("ClickHouse query failed: ")


def test_failed_query_logs_clickhouse_response_text():
    logged = []
    api = _load_api(logged)
    try:
        api._clickhouse_query("SELECT 1", {}, _SETTINGS)
    except Exception:  # noqa: BLE001
        pass
    titles = [t for t, _ in logged]
    assert "ClickHouse query failed" in titles
    body = next(m for t, m in logged if t == "ClickHouse query failed")
    assert body == _BODY[:1000]


def test_first_line_is_capped_at_300_chars():
    logged = []
    api = _load_api(logged)
    long_line = "Code: 47. " + "x" * 600
    api.requests.get = lambda *a, **k: _Resp(400, long_line + "\nmore")
    try:
        api._clickhouse_query("SELECT 1", {}, _SETTINGS)
    except Exception as exc:  # noqa: BLE001
        message = str(exc)
    assert long_line[:300] in message
    assert long_line[:301] not in message


def test_batch_read_error_names_the_clickhouse_reason():
    logged = []
    api = _load_api(logged)
    actuals = types.SimpleNamespace(
        fact_name="zz_actuals", scenario_key="actuals",
        clickhouse_table="epm_gold.zz_actuals", has_scenario_id=0, has_layer=0,
        reroute_table=None, reroute_column=None, reroute_measure=None,
    )
    api._get_fact = (
        lambda fact=None, scenario=None: actuals if fact == "zz_actuals" else None)
    api._get_fact_by_scenario = lambda scenario: None
    api._get_ch_connection = lambda: _SETTINGS

    result = api._batch_query_clickhouse([{
        "entity": "ZZ01", "year": 2026, "account": "ZZ100",
        "measure": "period_net_amount", "periods": (1,), "dimensions": {},
        "fact": "zz_actuals",
    }])
    assert result["values"] == [None]
    (err,) = result["errors"]
    assert _FIRST_LINE in err
    assert "ClickHouse query failed" in err
    # the body is logged once, not once more as a bare traceback
    assert [t for t, _ in logged].count("ClickHouse query failed") == 1
    assert logged[0][1] == _BODY[:1000]

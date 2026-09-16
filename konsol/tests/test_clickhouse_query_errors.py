"""A failed ClickHouse query says why (konsol#214, PR #217 review 1).

api._clickhouse_query used to raise a bare HTTP error and the batch reader
turned it into a generic "ClickHouse query failed", so an unknown column (for
example budget_scenario_id before the warehouse change is built) was
invisible. Now ClickHouse's response body is logged, and the message the
caller shows carries only ClickHouse's error code and exception name (row K7:
the first line can hold table names, SQL, the server version or the user
name, so those stay in the log). api.py is loaded under a private name with a
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


def _message(api):
    try:
        api._clickhouse_query("SELECT 1", {}, _SETTINGS)
    except Exception as exc:  # noqa: BLE001 — the message is what matters
        return str(exc)
    raise AssertionError("a 400 response must raise")


def test_failed_query_message_shows_code_and_exception_name_only():
    # K7: the user sees ClickHouse's code and exception name, never the
    # identifiers, SQL scope or server version from its first line.
    logged = []
    api = _load_api(logged)
    message = _message(api)
    assert message == "ClickHouse query failed (47: UNKNOWN_IDENTIFIER)"
    assert "budget_scenario_id" not in message
    assert "SELECT" not in message
    assert "DB::" not in message


def test_auth_failure_message_does_not_name_the_user():
    logged = []
    api = _load_api(logged)
    body = (
        "Code: 516. DB::Exception: zz_reader: Authentication failed: password "
        "is incorrect, or there is no user with such name. "
        "(AUTHENTICATION_FAILED) (version 24.3.1.1 (official build))\n")
    api.requests.get = lambda *a, **k: _Resp(401, body)
    message = _message(api)
    assert message == "ClickHouse query failed (516: AUTHENTICATION_FAILED)"
    assert "zz_reader" not in message
    assert "24.3" not in message
    # the log still gets the whole reply
    assert logged == [("ClickHouse query failed", body)]


def test_code_without_exception_name_shows_the_code():
    logged = []
    api = _load_api(logged)
    api.requests.get = lambda *a, **k: _Resp(
        400, "Code: 62. DB::Exception: Syntax error at zz_table\n")
    assert _message(api) == "ClickHouse query failed (62)"


def test_empty_body_gives_the_plain_message():
    logged = []
    api = _load_api(logged)
    api.requests.get = lambda *a, **k: _Resp(500, "")
    assert _message(api) == "ClickHouse query failed"


def test_unparsable_body_gives_the_plain_message():
    logged = []
    api = _load_api(logged)
    api.requests.get = lambda *a, **k: _Resp(
        502, "<html>Bad gateway at zz-clickhouse</html>")
    assert _message(api) == "ClickHouse query failed"


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
    assert err == "ClickHouse query failed (47: UNKNOWN_IDENTIFIER)"
    assert "budget_scenario_id" not in err
    # the body is logged once, not once more as a bare traceback
    assert [t for t, _ in logged].count("ClickHouse query failed") == 1
    assert logged[0][1] == _BODY[:1000]


def _recording_api(logged, status=200, text="1\n"):
    """api.py whose requests stub records every call as (method, url, kwargs)."""
    api = _load_api(logged)
    calls = []

    def record(method):
        def call(url, **kwargs):
            calls.append((method, url, kwargs))
            return _Resp(status, text)
        return call

    api.requests = types.SimpleNamespace(
        get=record("get"), post=record("post"), exceptions=requests.exceptions)
    return api, calls


def test_query_goes_in_the_body_not_the_url():
    """konsol#194: the SQL travels in the POST body, never in the URL.

    _clickhouse_query used to put the whole statement into the query string
    (``requests.get(url, params={**params, "query": sql})``). At the add-in's
    chunk size a dense sheet's SQL ran past ClickHouse's 128 KiB form-field
    limit and every chunk failed with "Poco::Exception. Code: 1000 — HTML Form
    Exception: Field value too long" (1,500 cells in one group were fine,
    1,800 were not). konsol.clickhouse.execute already POSTs its body; this
    helper must have the same shape, with only the param_* values in the URL.
    """
    logged = []
    api, calls = _recording_api(logged)

    accounts = ", ".join(f"'ZZ{i:05d}'" for i in range(20_000))
    sql = f"SELECT sum(x) FROM epm_gold.zz_actuals WHERE main_account IN ({accounts})"
    assert len(sql.encode("utf-8")) > 128 * 1024, "fixture must exceed 128 KiB"

    result = api._clickhouse_query(sql, {"param_entity": "ZZ01"}, _SETTINGS)

    assert result == "1"
    methods = [method for method, _, _ in calls]
    assert methods == ["post"], (
        "the SQL must be POSTed in the body, not fetched with it in the URL; "
        f"calls were {methods}")
    _, _, kwargs = calls[0]
    assert kwargs.get("data") == sql.encode("utf-8"), "body must be the UTF-8 SQL"
    url_params = kwargs.get("params") or {}
    assert "query" not in url_params, "the SQL must not be duplicated into the URL"
    assert url_params.get("param_entity") == "ZZ01", "param_* values stay in the URL"

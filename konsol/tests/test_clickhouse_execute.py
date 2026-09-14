"""TDD test for konsol.clickhouse.execute — SQL must go in the POST body.

konsol#188: _sync_table_inner batches INSERTs at 1000 rows. execute() put the
whole SQL statement into the URL query string
(``query_params["query"] = sql; requests.post(url, params=query_params, ...)``),
so any batch whose bytes ran past ClickHouse's HTTP form-field limit failed
with HTTP 500 "Field value too long" — and sync_table's ``except HTTPError``
swallowed it, so the table looked synced but stayed empty.
epm_staging.consolidation_ancestry's long ``path`` strings were the first
column wide enough to trip it (2206 rows was already past the ceiling).
"""
import importlib.util
import os
import sys
import types
from unittest.mock import patch

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CH_PATH = os.path.join(APP_DIR, "clickhouse.py")

try:
    import requests  # noqa: F401 — clickhouse.py imports it at module level
except ModuleNotFoundError:  # host runner without requests — runner skips
    raise ImportError("needs requests")


def _load_clickhouse():
    """Load clickhouse.py against a stub frappe (pattern from
    test_write_through_contract.py's _load_clickhouse)."""
    frappe = types.ModuleType("frappe")
    frappe.flags = types.SimpleNamespace(
        in_install=False, in_import=False, in_migrate=False, in_patch=False)
    frappe._logger = types.SimpleNamespace(
        warning=lambda *a, **k: None, error=lambda *a, **k: None,
        info=lambda *a, **k: None)
    frappe.logger = lambda: frappe._logger
    frappe.publish_realtime = lambda *a, **k: None

    model = types.ModuleType("frappe.model")
    base_document = types.ModuleType("frappe.model.base_document")
    base_document.get_controller = lambda dt: None
    model.base_document = base_document
    frappe.model = model

    sys.modules.update({"frappe": frappe, "frappe.model": model,
                        "frappe.model.base_document": base_document})
    spec = importlib.util.spec_from_file_location(
        "konsol_clickhouse_under_test_execute", CH_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module, frappe


class _Resp:
    text = "ok"

    def raise_for_status(self):
        pass


def test_execute_sends_sql_in_body():
    """A large INSERT (bigger than the ~200KB that trips ClickHouse's 'Field
    value too long' when the query lands in the URL) must reach requests.post
    as `data` — UTF-8 encoded bytes, including non-ASCII content — while the
    URL `params` carry only the caller's own params (e.g. param_x for a
    parameterised query) and never a `query` key."""
    m, _ = _load_clickhouse()
    m.get_connection = lambda: {
        "host": "localhost", "port": "8123", "user": "default",
        "password": "", "secure": False, "verify": True,
    }

    long_value = "é" * 50_000  # non-ASCII; 2 bytes/char in UTF-8
    values_sql = ", ".join(f"('row{i}', '{long_value}')" for i in range(4))
    sql = f"INSERT INTO epm_staging.consolidation_ancestry (a, path) VALUES {values_sql}"
    assert len(sql.encode("utf-8")) > 200_000, "fixture must exceed 200KB"

    with patch.object(m.requests, "post", return_value=_Resp()) as post:
        result = m.execute(sql, params={"param_x": "1"})

    assert result == "ok"
    post.assert_called_once()
    _, kwargs = post.call_args

    body = kwargs.get("data")
    assert body is not None, "SQL must be sent as the POST body, not the URL query string"
    assert isinstance(body, bytes), "body must be UTF-8 encoded bytes"
    assert body == sql.encode("utf-8")
    assert "é".encode("utf-8") in body

    url_params = kwargs.get("params") or {}
    assert "query" not in url_params, "SQL must not be duplicated into the URL params"
    assert url_params.get("param_x") == "1"


class _FailingResp:
    """A ClickHouse HTTP error: the status is generic, the body says why."""
    status_code = 500
    text = "Code: 47. DB::Exception: Unknown expression identifier 'amount_basis' in scope ... (UNKNOWN_IDENTIFIER)"

    def raise_for_status(self):
        raise requests.HTTPError("500 Server Error: Internal Server Error for url: http://ch:8123/")


def test_execute_error_carries_clickhouse_body():
    """konsolidat#199 (PR #201 review): callers classify ClickHouse errors by
    their text (tasks._batches_without_basis looks for UNKNOWN_IDENTIFIER /
    Missing columns). requests' HTTPError message is only "500 Server Error
    ... for url", so the body must be put into the raised error."""
    module, _ = _load_clickhouse()
    module.get_connection = lambda: {"user": "u", "password": "p", "verify": False}
    module.connection_url = lambda conn: "http://ch:8123/"
    with patch.object(module.requests, "post", return_value=_FailingResp()):
        try:
            module.execute("SELECT argMax(amount_basis, claimed_at) FROM t")
        except Exception as e:  # noqa: BLE001
            message = str(e)
        else:
            raise AssertionError("execute() did not raise on HTTP 500")
    assert "UNKNOWN_IDENTIFIER" in message and "amount_basis" in message, message
    assert "500" in message

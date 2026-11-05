"""A Measure expression is checked the way dbt will run it (konsol#229, option C).

Before this, ``Measure`` had no validate(): ``sum(no_such_column)`` saved,
published and requested a governed rebuild, and the failure surfaced later as
a ClickHouse UNKNOWN_IDENTIFIER in the dbt build, naming a column, not the
measure. Chosen under Deepak Pai's standing rule of 5 Nov 2026 (product,
configurable, not opinionated); HMD table on konsol#229.

``konsol.measure_check`` is loaded by path with a stub frappe. ClickHouse is a
fake ``run(sql, params)`` that answers the way ``konsol.clickhouse.execute``
does: the response text, or ``requests.HTTPError`` carrying ClickHouse's
message.
"""
import importlib.util
import os
import sys
import types

import requests

_HERE = os.path.dirname(os.path.abspath(__file__))
_SRC = os.path.join(_HERE, "..", "measure_check.py")


class _ValidationError(Exception):
    pass


_MESSAGES = []


def _throw(msg, exc=None, **kwargs):
    raise (exc or _ValidationError)(msg)


_frappe = types.ModuleType("frappe")
_frappe.throw = _throw
_frappe.ValidationError = _ValidationError
_frappe.msgprint = lambda msg, **k: _MESSAGES.append((msg, k))
sys.modules["frappe"] = _frappe

#: EPM Settings as the check reads it (review of #391: a dedicated user).
_SETTINGS = {"clickhouse_host": "clickhouse", "clickhouse_port": "8123",
             "clickhouse_check_user": "konsol_measure_check",
             "clickhouse_check_password": "zz-secret"}


class _Settings:
    def __getattr__(self, key):
        return _SETTINGS.get(key)

    def get_password(self, field, raise_exception=True):
        return _SETTINGS.get(field)


_frappe.get_single = lambda doctype: _Settings()

#: Every statement sent as the site's MAIN ClickHouse user. The check must
#: never send one: an expression runs as the check user or not at all.
_MAIN_USER_CALLS = []
_konsol = types.ModuleType("konsol")
_konsol.__path__ = []
_clickhouse = types.ModuleType("konsol.clickhouse")
_clickhouse.execute = lambda sql, params=None: _MAIN_USER_CALLS.append(sql) or ""
_clickhouse.get_connection = lambda: {"host": "clickhouse", "port": "8123", "user": "default",
                                      "password": "main", "secure": False, "verify": True}
_clickhouse.connection_url = lambda conn: f"http://{conn['host']}:{conn['port']}/"
sys.modules.setdefault("konsol", _konsol)
sys.modules["konsol.clickhouse"] = _clickhouse

_spec = importlib.util.spec_from_file_location("measure_check_under_test_229", _SRC)
_m = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_m)

NAME = "zz_period_amount"
COLUMNS = ["data_area_id", "fiscal_year", "fiscal_period", "main_account",
           "debit_amount", "credit_amount", "dim_zz_region"]


def _ch_error(text):
    return requests.HTTPError(f"500 from ClickHouse: {text}")


class _Warehouse:
    """A fake ClickHouse: refuses what real ClickHouse would for these cases."""

    def __init__(self, error=None, probe="1"):
        self.error = error
        self.probe = probe
        self.calls = []

    def __call__(self, sql, params=None):
        self.calls.append((sql, params))
        if sql.startswith("DESCRIBE"):
            return "\n".join(f"{c}\tString" for c in COLUMNS)
        if sql.startswith("EXISTS"):
            if isinstance(self.probe, Exception):
                raise self.probe
            return self.probe
        if self.error:
            raise self.error
        return ""

    def statements(self):
        return [sql for sql, _ in self.calls if not sql.startswith(("EXISTS", "DESCRIBE"))]


def _refused(fn):
    try:
        fn()
    except _ValidationError as exc:
        return str(exc)
    raise AssertionError("not refused")


# --- the static rule: one expression on one line ---------------------------


def test_blank_and_multi_statement_expressions_are_refused_without_the_warehouse():
    wh = _Warehouse()
    for bad in ("", "   ", "sum(x); select 1", "sum(x) -- tail", "sum(x) /* c */",
                "sum(x)\n, sum(y)"):
        msg = _refused(lambda: _m.check_measure(NAME, bad, publishing=True,
                                                compiled=[NAME], run=wh))
        assert NAME in msg, msg
    assert wh.calls == [], "the static rule must not need the warehouse"


def test_the_static_rule_applies_to_a_draft_save_too():
    msg = _refused(lambda: _m.check_measure(NAME, "sum(x);", publishing=False, compiled=[]))
    assert "semicolon" in msg, msg


# --- the dry run --------------------------------------------------------------


def test_the_dry_run_is_the_expression_as_dbt_writes_it_read_only_where_0():
    wh = _Warehouse()
    assert _m.check_measure(NAME, "sum(debit_amount)", publishing=True,
                            compiled=[NAME], run=wh)["status"] == "checked"
    (sql,) = wh.statements()
    assert "sum(debit_amount) as `zz_period_amount`" in sql, sql
    assert "FROM epm_silver.silver_gl_entries" in sql, sql
    assert "GROUP BY data_area_id, fiscal_year, fiscal_period, main_account" in sql, sql
    # WHERE 0, not LIMIT 0: nothing is read or aggregated (review of #391).
    assert "WHERE 0" in sql and "LIMIT" not in sql, sql


def test_every_statement_carries_a_server_side_time_limit():
    """readonly, max_execution_time and cancel-on-close on every request, so a
    slow expression is stopped by ClickHouse, not only by the HTTP timeout."""
    wh = _Warehouse()
    _m.check_measure(NAME, "sum(debit_amount)", publishing=True, compiled=[NAME], run=wh)
    for sql, params in wh.calls:
        assert params == {"readonly": "1", "max_execution_time": "5",
                          "cancel_http_readonly_queries_on_client_close": "1"}, (sql, params)


def test_an_unknown_column_is_refused_naming_the_measure_and_the_columns():
    wh = _Warehouse(error=_ch_error(
        "Code: 47. DB::Exception: Missing columns: 'no_such_column' while processing "
        "query. (UNKNOWN_IDENTIFIER) (version 24.8.4.13 (official build))"))
    msg = _refused(lambda: _m.check_measure(NAME, "sum(no_such_column)", publishing=True,
                                            compiled=[NAME], run=wh))
    assert NAME in msg and "no_such_column" in msg, msg
    assert "debit_amount" in msg and "dim_zz_region" in msg, msg


def test_a_non_aggregate_is_refused():
    wh = _Warehouse(error=_ch_error(
        "Code: 215. DB::Exception: Column `debit_amount` is not under aggregate function "
        "and not in GROUP BY. (NOT_AN_AGGREGATE)"))
    msg = _refused(lambda: _m.check_measure(NAME, "debit_amount", publishing=True,
                                            compiled=[NAME], run=wh))
    assert "NOT_AN_AGGREGATE" in msg, msg


def test_a_warehouse_that_cannot_answer_the_probe_is_reported_not_silent_and_not_refused():
    """Whether the warehouse can answer is decided by a probe (EXISTS TABLE as
    the check user), not by the expression's error name (review of #391)."""
    for probe in (
        _ch_error("Code: 516. DB::Exception: konsol_measure_check: Authentication failed. "
                  "(AUTHENTICATION_FAILED)"),
        requests.ConnectionError("refused"),
        _ch_error("502 Bad Gateway"),
        "0",  # the table is not built yet on this site
    ):
        del _MESSAGES[:]
        wh = _Warehouse(probe=probe)
        result = _m.check_measure(NAME, "sum(debit_amount)", publishing=True,
                                  compiled=[NAME], run=wh)
        assert result["status"] == "not checked", (probe, result)
        assert wh.statements() == [], "the expression ran although the probe failed"
        assert len(_MESSAGES) == 1 and NAME in _MESSAGES[0][0], _MESSAGES
        assert "not checked" in _MESSAGES[0][0], _MESSAGES
        assert _MESSAGES[0][1].get("indicator") == "orange", _MESSAGES


def test_once_the_probe_answers_any_error_from_the_expression_refuses_the_save():
    """ACCESS_DENIED, UNKNOWN_TABLE or a timeout from the expression itself is
    the expression's fault once the probe has shown the warehouse answers."""
    for name in ("ACCESS_DENIED", "UNKNOWN_TABLE", "TIMEOUT_EXCEEDED", "NOT_ENOUGH_PRIVILEGES",
                 "MEMORY_LIMIT_EXCEEDED"):
        wh = _Warehouse(error=_ch_error(f"Code: 497. DB::Exception: zz. ({name})"))
        msg = _refused(lambda: _m.check_measure(NAME, "sum(toUInt8(debit_amount IN zz.t))",
                                                publishing=True, compiled=[NAME], run=wh))
        assert NAME in msg, (name, msg)


def test_an_unconfigured_check_user_is_not_checked_and_never_uses_the_main_user():
    """No fallback to the site's main ClickHouse user, which can read every
    table in the shared warehouse (review of #391)."""
    for missing in ("clickhouse_check_user", "clickhouse_check_password"):
        saved = _SETTINGS[missing]
        _SETTINGS[missing] = None
        del _MESSAGES[:]
        del _MAIN_USER_CALLS[:]
        try:
            result = _m.check_measure(NAME, "sum(debit_amount)", publishing=True, compiled=[NAME])
        finally:
            _SETTINGS[missing] = saved
        assert result["status"] == "not checked", result
        assert "the measure-check ClickHouse user is not configured" in result["line"], result
        assert _MAIN_USER_CALLS == [], _MAIN_USER_CALLS
        assert _MESSAGES and _MESSAGES[0][1].get("indicator") == "orange", _MESSAGES


def test_the_check_connects_as_the_check_user_with_a_short_timeout():
    sent = []

    class _Resp:
        status_code = 200
        text = "1"

    def _post(url, params=None, data=None, auth=None, timeout=None, verify=None):
        sent.append((url, auth, timeout))
        return _Resp()

    real = _m.requests_post
    _m.requests_post = _post
    try:
        run = _m.checker()
        assert run("EXISTS TABLE epm_silver.silver_gl_entries", _m.SETTINGS) == "1"
    finally:
        _m.requests_post = real
    (url, auth, timeout), = sent
    assert url == "http://clickhouse:8123/", url
    assert auth == ("konsol_measure_check", "zz-secret"), auth
    assert timeout and timeout <= 10, timeout


def test_subqueries_table_functions_and_system_tables_are_refused_before_sending():
    """Defence in depth only: the control is the check user's grants."""
    wh = _Warehouse()
    for bad in ("sum(toUInt8((SELECT any(name) FROM system.users)))",
                "sum(toUInt8((select 1)))",
                "sum(toUInt8(debit_amount IN system.users))",
                "sum(length(url('http://x', 'CSV')))",
                "sum(toUInt8(debit_amount IN remote('h', 'db', 't')))"):
        msg = _refused(lambda: _m.check_measure(NAME, bad, publishing=True,
                                                compiled=[NAME], run=wh))
        assert NAME in msg, msg
    assert wh.calls == []
    # a column whose name merely contains "select" is not a subquery
    assert _m.static_problem("sum(selected_amount)") is None


def test_the_error_name_is_the_last_one_clickhouse_gives():
    """ClickHouse echoes the expression, which may itself contain "(NAME)"."""
    text = ("Code: 47. DB::Exception: Missing columns: 'x' while processing query: "
            "'SELECT sum(x) (ZZ_ECHOED) FROM t'. (UNKNOWN_IDENTIFIER) (version 24.8.14.39)")
    assert _m._error_name(text) == "UNKNOWN_IDENTIFIER"


def test_a_measure_dbt_does_not_compile_is_not_dry_run():
    wh = _Warehouse(error=_ch_error("Code: 47. (UNKNOWN_IDENTIFIER)"))
    assert _m.check_measure(NAME, "sum(budget_amount)", publishing=True,
                            compiled=["zz_other"], run=wh)["status"] == "not compiled"
    assert wh.calls == []


def test_a_save_that_does_not_publish_the_expression_is_not_dry_run():
    wh = _Warehouse()
    assert _m.check_measure(NAME, "sum(debit_amount)", publishing=False,
                            compiled=[NAME], run=wh) is None
    assert wh.calls == []


def test_epm_settings_holds_the_check_user_beside_the_main_connection():
    """Configured the way the main ClickHouse credentials are: EPM Settings,
    password in a Password field (never in git), set by the configurator
    through install.setup_epm_settings."""
    import ast
    import json

    settings = os.path.join(_HERE, "..", "pipeline", "doctype", "epm_settings", "epm_settings.json")
    with open(settings) as f:
        fields = {x["fieldname"]: x for x in json.load(f)["fields"]}
    assert fields.get("clickhouse_check_user", {}).get("fieldtype") == "Data", fields.keys()
    assert fields.get("clickhouse_check_password", {}).get("fieldtype") == "Password"

    with open(os.path.join(_HERE, "..", "install.py")) as f:
        tree = ast.parse(f.read())
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "setup_epm_settings")
    args = [a.arg for a in fn.args.args]
    assert "ch_check_user" in args and "ch_check_password" in args, args

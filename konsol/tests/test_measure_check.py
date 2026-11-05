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

    def __init__(self, error=None):
        self.error = error
        self.calls = []

    def __call__(self, sql, params=None):
        self.calls.append((sql, params))
        if sql.startswith("DESCRIBE"):
            return "\n".join(f"{c}\tString" for c in COLUMNS)
        if self.error:
            raise self.error
        return ""


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


def test_the_dry_run_is_the_expression_as_dbt_writes_it_read_only_and_limit_0():
    wh = _Warehouse()
    assert _m.check_measure(NAME, "sum(debit_amount)", publishing=True,
                            compiled=[NAME], run=wh) == "checked"
    (sql, params), = wh.calls
    assert "sum(debit_amount) as `zz_period_amount`" in sql, sql
    assert "FROM epm_silver.silver_gl_entries" in sql, sql
    assert "GROUP BY data_area_id, fiscal_year, fiscal_period, main_account" in sql, sql
    assert sql.rstrip().endswith("LIMIT 0"), sql
    assert params == {"readonly": "1"}, params


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


def test_a_warehouse_that_cannot_answer_is_reported_not_silent_and_not_refused():
    for error in (
        _ch_error("Code: 60. DB::Exception: Table epm_silver.silver_gl_entries does not "
                  "exist. (UNKNOWN_TABLE)"),
        requests.ConnectionError("refused"),
        _ch_error("502 Bad Gateway"),
    ):
        del _MESSAGES[:]
        wh = _Warehouse(error=error)
        assert _m.check_measure(NAME, "sum(debit_amount)", publishing=True,
                                compiled=[NAME], run=wh) == "unchecked", error
        assert len(_MESSAGES) == 1 and NAME in _MESSAGES[0][0], _MESSAGES
        assert "not checked" in _MESSAGES[0][0], _MESSAGES


def test_a_measure_dbt_does_not_compile_is_not_dry_run():
    wh = _Warehouse(error=_ch_error("Code: 47. (UNKNOWN_IDENTIFIER)"))
    assert _m.check_measure(NAME, "sum(budget_amount)", publishing=True,
                            compiled=["zz_other"], run=wh) == "not compiled"
    assert wh.calls == []


def test_a_save_that_does_not_publish_the_expression_is_not_dry_run():
    wh = _Warehouse()
    assert _m.check_measure(NAME, "sum(debit_amount)", publishing=False,
                            compiled=[NAME], run=wh) is None
    assert wh.calls == []

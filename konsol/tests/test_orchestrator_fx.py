"""PRD-18 — FX surfacing (pure core).

Host tests for the pure :mod:`konsol.orchestrator.fx` module: ``build_fx_query``
(safe SQL over ``epm_silver.silver_exchange_rates``, filterable by from/to
currency, as-of date, rate type and source — injection-safe currency codes) and
``normalize_fx_rows`` (shape raw ClickHouse result rows into the canonical
``{from, to, rate, as_of, type, source}`` dicts, incl. the empty case). No
frappe at top level. The frappe/CH-bound ``get_fx_rates`` API is guarded with
``importorskip``.
"""
import os

import pytest

from konsol.orchestrator import fx


ORCH_DIR = os.path.dirname(fx.__file__)


# ---- purity / no top-level frappe ------------------------------------------

def test_fx_module_has_no_toplevel_frappe_import():
    with open(os.path.join(ORCH_DIR, "fx.py")) as fh:
        src = fh.read()
    for line in src.splitlines():
        if line.startswith("import frappe") or line.startswith("from frappe"):
            raise AssertionError("fx.py must not import frappe at top level")


# ---- build_fx_query: filter combinations -----------------------------------

def test_build_fx_query_no_filter():
    sql = fx.build_fx_query({})
    assert "epm_silver.silver_exchange_rates" in sql
    assert "WHERE" not in sql


def test_build_fx_query_none_filters():
    sql = fx.build_fx_query(None)
    assert "epm_silver.silver_exchange_rates" in sql
    assert "WHERE" not in sql


def test_build_fx_query_from_only():
    sql = fx.build_fx_query({"from_currency": "USD"})
    assert "WHERE" in sql
    assert "from_currency = 'USD'" in sql
    assert "to_currency =" not in sql


def test_build_fx_query_from_and_to():
    sql = fx.build_fx_query({"from_currency": "USD", "to_currency": "JPY"})
    assert "from_currency = 'USD'" in sql
    assert "to_currency = 'JPY'" in sql
    assert " AND " in sql


def test_build_fx_query_as_of():
    sql = fx.build_fx_query({"as_of": "2026-01-31"})
    assert "as_of <= '2026-01-31'" in sql


def test_build_fx_query_rate_type():
    sql = fx.build_fx_query({"rate_type": "Spot"})
    assert "rate_type = 'Spot'" in sql


def test_build_fx_query_source():
    sql = fx.build_fx_query({"source": "manual"})
    assert "source = 'manual'" in sql


def test_build_fx_query_combined():
    sql = fx.build_fx_query(
        {
            "from_currency": "USD",
            "to_currency": "JPY",
            "as_of": "2026-01-31",
            "rate_type": "Spot",
            "source": "d365",
        }
    )
    assert "from_currency = 'USD'" in sql
    assert "to_currency = 'JPY'" in sql
    assert "as_of <= '2026-01-31'" in sql
    assert "rate_type = 'Spot'" in sql
    assert "source = 'd365'" in sql
    # all five joined by AND -> four AND separators
    assert sql.count(" AND ") == 4


# ---- build_fx_query: injection safety --------------------------------------

def test_build_fx_query_rejects_bad_from_currency():
    with pytest.raises(ValueError):
        fx.build_fx_query({"from_currency": "'; DROP TABLE x; --"})


def test_build_fx_query_rejects_bad_to_currency():
    with pytest.raises(ValueError):
        fx.build_fx_query({"to_currency": "US"})


def test_build_fx_query_rejects_four_letter_currency():
    with pytest.raises(ValueError):
        fx.build_fx_query({"from_currency": "USDX"})


def test_build_fx_query_uppercases_currency():
    sql = fx.build_fx_query({"from_currency": "usd"})
    assert "from_currency = 'USD'" in sql


def test_build_fx_query_rejects_bad_date():
    with pytest.raises(ValueError):
        fx.build_fx_query({"as_of": "2026/01/31"})


def test_build_fx_query_rejects_injection_date():
    with pytest.raises(ValueError):
        fx.build_fx_query({"as_of": "2026-01-31' OR '1'='1"})


def test_build_fx_query_rejects_bad_rate_type():
    with pytest.raises(ValueError):
        fx.build_fx_query({"rate_type": "Spot'; DROP"})


def test_build_fx_query_rejects_bad_source():
    with pytest.raises(ValueError):
        fx.build_fx_query({"source": "x' OR '1'='1"})


# ---- normalize_fx_rows -----------------------------------------------------

def test_normalize_fx_rows_empty():
    assert fx.normalize_fx_rows([], ["from_currency", "to_currency"]) == []


def test_normalize_fx_rows_none():
    assert fx.normalize_fx_rows(None, None) == []


def test_normalize_fx_rows_shaping():
    cols = ["from_currency", "to_currency", "rate", "as_of", "rate_type", "source"]
    rows = [["USD", "JPY", "102.5", "2026-01-31", "Spot", "d365"]]
    out = fx.normalize_fx_rows(rows, cols)
    assert out == [
        {
            "from": "USD",
            "to": "JPY",
            "rate": "102.5",
            "as_of": "2026-01-31",
            "type": "Spot",
            "source": "d365",
        }
    ]


def test_normalize_fx_rows_alt_column_names():
    # raw silver column names map to the canonical output keys too
    cols = ["from_currency", "to_currency", "exchange_rate", "valid_from", "exchange_rate_type"]
    rows = [["EUR", "USD", "1.1", "2026-02-01", "Average"]]
    out = fx.normalize_fx_rows(rows, cols)
    assert out[0]["from"] == "EUR"
    assert out[0]["to"] == "USD"
    assert out[0]["rate"] == "1.1"
    assert out[0]["as_of"] == "2026-02-01"
    assert out[0]["type"] == "Average"
    # missing source defaults to None
    assert out[0]["source"] is None


def test_normalize_fx_rows_multiple():
    cols = ["from_currency", "to_currency", "rate", "as_of", "rate_type", "source"]
    rows = [
        ["USD", "JPY", "102.5", "2026-01-31", "Spot", "d365"],
        ["JPY", "USD", "0.0097", "2026-01-31", "Spot", "d365"],
    ]
    out = fx.normalize_fx_rows(rows, cols)
    assert len(out) == 2
    assert out[1]["from"] == "JPY"
    # every record carries all six canonical keys
    for rec in out:
        assert set(rec) == {"from", "to", "rate", "as_of", "type", "source"}


# ---- frappe/CH-bound API (guarded) -----------------------------------------

def test_get_fx_rates_exists_and_callable():
    assert callable(fx.get_fx_rates)
    # __name__ preserved through the whitelist decorator
    assert fx.get_fx_rates.__name__ == "get_fx_rates"


def test_get_fx_rates_smoke():
    frappe = pytest.importorskip("frappe")  # noqa: F841 - skips on host
    assert callable(fx.get_fx_rates)

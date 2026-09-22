"""Read-token resolution against a declared fiscal calendar, pure:
konsol/period_read_model.py (konsol#251).

Loaded by path; the module imports nothing from frappe or konsol.

The calendar is the customer's declaration, not a constant in this app. A read
token (12, "CLS", "Q3", "FY") resolves against the periods that customer
declared for that year, so a 13-period year, a 4-4-5 year and a plain monthly
year all answer from the same code.
"""
import importlib.util
import os

import pytest

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_spec = importlib.util.spec_from_file_location(
    "prm_under_test", os.path.join(APP_DIR, "period_read_model.py"))
M = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(M)


def _monthly_with_close():
    """The shape the live site declares: OPN, P01-P12 with quarters, CLS."""
    rows = [{"fiscal_period": 0, "period_code": "OPN",
             "period_type": "Opening", "quarter": ""}]
    for p in range(1, 13):
        rows.append({
            "fiscal_period": p,
            "period_code": "P%02d" % p,
            "period_type": "Regular",
            "quarter": "Q%d" % ((p - 1) // 3 + 1),
        })
    rows.append({"fiscal_period": 13, "period_code": "CLS",
                 "period_type": "Closing", "quarter": ""})
    return rows


# --- single periods -------------------------------------------------------

def test_declared_closing_period_resolves():
    """konsol#251: period 13 is declared, so a read must reach it.

    This is the whole bug: _resolve_period clamped to 1-12, so the only
    figure a user could obtain for a closed year was the pre-close one.
    """
    assert M.resolve_period(13, _monthly_with_close()) == (13,)


def test_closing_period_resolves_by_its_code():
    assert M.resolve_period("CLS", _monthly_with_close()) == (13,)


def test_opening_period_resolves():
    assert M.resolve_period(0, _monthly_with_close()) == (0,)
    assert M.resolve_period("OPN", _monthly_with_close()) == (0,)


def test_regular_period_resolves():
    assert M.resolve_period(7, _monthly_with_close()) == (7,)
    assert M.resolve_period("P07", _monthly_with_close()) == (7,)


def test_period_codes_are_case_insensitive():
    assert M.resolve_period("cls", _monthly_with_close()) == (13,)


# --- ranges ---------------------------------------------------------------

def test_quarters_come_from_the_declared_quarter_column():
    rows = _monthly_with_close()
    assert M.resolve_period("Q1", rows) == (1, 2, 3)
    assert M.resolve_period("Q3", rows) == (7, 8, 9)


def test_halves_are_built_from_the_declared_quarters():
    rows = _monthly_with_close()
    assert M.resolve_period("H1", rows) == (1, 2, 3, 4, 5, 6)
    assert M.resolve_period("H2", rows) == (7, 8, 9, 10, 11, 12)


def test_fy_is_the_regular_periods_only():
    """FY stays the year's movements: including the close would zero a P&L
    account, because the close IS the reversal (10,079 P&L rows at period 13
    summing to +131,866,388,549 against 564 balance-sheet rows at the
    negative of it, measured on the live site 22 Sep 2026)."""
    assert M.resolve_period("FY", _monthly_with_close()) == tuple(range(1, 13))


def test_fy_excludes_opening_and_closing_even_when_declared():
    rows = _monthly_with_close()
    got = M.resolve_period("FY", rows)
    assert 0 not in got
    assert 13 not in got


# --- a different customer's calendar --------------------------------------

def test_a_thirteen_period_year_resolves_its_own_periods():
    """A 4-week/13-period customer: P13 is a Regular period, not the close,
    and FY must include it."""
    rows = [{"fiscal_period": p, "period_code": "P%02d" % p,
             "period_type": "Regular", "quarter": "Q%d" % min((p - 1) // 3 + 1, 4)}
            for p in range(1, 14)]
    rows.append({"fiscal_period": 14, "period_code": "CLS",
                 "period_type": "Closing", "quarter": ""})
    assert M.resolve_period(13, rows) == (13,)
    assert M.resolve_period("FY", rows) == tuple(range(1, 14))
    assert M.resolve_period("CLS", rows) == (14,)


def test_a_year_without_a_close_refuses_cls():
    rows = [r for r in _monthly_with_close() if r["period_type"] != "Closing"]
    with pytest.raises(ValueError) as exc:
        M.resolve_period("CLS", rows)
    assert "CLS" in str(exc.value)


# --- refusals name what IS declared ---------------------------------------

def test_undeclared_period_is_refused_naming_the_declared_set():
    with pytest.raises(ValueError) as exc:
        M.resolve_period(14, _monthly_with_close())
    msg = str(exc.value)
    assert "14" in msg
    assert "13" in msg, "the refusal must name the periods that ARE declared"


def test_unknown_token_is_refused():
    with pytest.raises(ValueError):
        M.resolve_period("Q9", _monthly_with_close())
    with pytest.raises(ValueError):
        M.resolve_period("banana", _monthly_with_close())


def test_an_undeclared_year_refuses_rather_than_assuming_twelve_months():
    """No silent fallback: an empty calendar is a refusal, not 1-12.

    konsol#210/konsolidat#235 are exactly this failure the other way round --
    a fallback that was the whole path and warned about nothing.
    """
    with pytest.raises(ValueError) as exc:
        M.resolve_period("FY", [])
    assert "declar" in str(exc.value).lower()


# --- aggregation ----------------------------------------------------------

def test_sum_measure_aggregates_and_combines_by_sum():
    assert M.sql_aggregate("sum", "period_net_amount") == "sum(period_net_amount)"
    assert M.combine_periods("sum", [(7, 1.0), (8, 2.0), (9, 4.0)]) == 7.0


def test_last_measure_takes_the_highest_declared_period():
    """konsol#251: a cumulative balance summed across periods is nonsense.

    Measured live 22 Sep 2026: K.EPM("JP_ECL", 2025, "FY", "3100",
    "ytd_net_amount") returned -9,825,228,728.88, the sum of six cumulative
    balances, where the balance at period 12 is 449,131,396.42.
    """
    assert M.sql_aggregate("last", "cumulative_balance") == \
        "argMax(cumulative_balance, fiscal_period)"
    rows = [(7, -11938798126.25), (8, 398328479.21), (9, 408489062.66),
            (10, 428810229.54), (11, 428810229.54), (12, 449131396.42)]
    assert M.combine_periods("last", rows) == 449131396.42


def test_last_ignores_row_order():
    rows = [(12, 449131396.42), (7, -11938798126.25), (9, 408489062.66)]
    assert M.combine_periods("last", rows) == 449131396.42


def test_avg_measure_averages_the_periods_that_have_rows():
    assert M.sql_aggregate("avg", "variance_pct") == "avg(variance_pct)"
    assert M.combine_periods("avg", [(7, 2.0), (8, 4.0)]) == 3.0


def test_a_missing_period_contributes_nothing_rather_than_a_zero():
    """A period the warehouse has no row for must not drag an average down,
    and must not become the 'last' value."""
    assert M.combine_periods("avg", [(7, 4.0)]) == 4.0
    assert M.combine_periods("last", [(7, 4.0)]) == 4.0
    assert M.combine_periods("sum", []) == 0.0
    assert M.combine_periods("last", []) == 0.0


def test_an_unknown_aggregation_is_refused_not_silently_summed():
    with pytest.raises(ValueError):
        M.sql_aggregate("median", "period_net_amount")
    with pytest.raises(ValueError):
        M.combine_periods("median", [(7, 1.0)])

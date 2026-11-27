"""Driver sources, pure: fixed weights, derived (Account Set) values and the
derived query (konsol#269, phase 2b-1; Deepak Pai picked A on Q1 and Q2,
27 Nov 2026).

Loaded by path; the module imports nothing from frappe or konsol.
"""
import contextlib
import importlib.util
import os
from datetime import date
from decimal import Decimal
from fractions import Fraction

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PATH = os.path.join(APP_DIR, "cost_allocation_model.py")
_spec = importlib.util.spec_from_file_location("cost_allocation_sources_under_test", _PATH)
M = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(M)

JAN = (date(2026, 1, 1), date(2026, 1, 31))


@contextlib.contextmanager
def raises(exc):
    box = type("Box", (), {})()
    try:
        yield box
    except exc as e:
        box.value = e
        return
    raise AssertionError("did not raise %s" % exc.__name__)


def _row(member, value, effective_from, effective_to=None):
    return {"member": member, "value": value,
            "effective_from": effective_from, "effective_to": effective_to}


# --- fixed_values -------------------------------------------------------------

def test_fixed_values_picks_the_covering_window_per_receiver():
    rows = [
        _row("Sales", 3, date(2025, 1, 1), date(2025, 12, 31)),   # expired
        _row("Sales", 5, date(2026, 1, 1)),                       # open-ended, covers
        _row("Ops", "2.5", date(2024, 6, 1), date(2026, 1, 31)),  # ends on the last day: covers
    ]
    out = M.fixed_values(rows, ["Sales", "Ops"], *JAN)
    assert out == {"Sales": Fraction(5), "Ops": Fraction(5, 2)}
    assert all(isinstance(v, Fraction) for v in out.values())


def test_fixed_values_open_ended_window_covers_any_later_period():
    rows = [_row("Sales", 1, date(2020, 1, 1))]
    assert M.fixed_values(rows, ["Sales"], date(2031, 7, 1), date(2031, 7, 31)) == {"Sales": Fraction(1)}


def test_fixed_values_feed_weights():
    rows = [_row("Sales", 3, date(2026, 1, 1)), _row("Ops", 1, date(2026, 1, 1))]
    vals = M.fixed_values(rows, ["Sales", "Ops"], *JAN)
    w = M.weights([{"step": 1, "receiver_member": m, "driver_value": v} for m, v in vals.items()])
    assert w == {(1, "Sales"): Fraction(3, 4), (1, "Ops"): Fraction(1, 4)}


def test_fixed_values_refuses_a_receiver_with_no_covering_window():
    rows = [_row("Sales", 1, date(2026, 1, 1)),
            _row("Ops", 1, date(2026, 2, 1))]  # starts after the period
    with raises(M.CostAllocationError) as e:
        M.fixed_values(rows, ["Sales", "Ops", "HR"], *JAN)
    errs = e.value.errors
    assert any("HR" in x and "no fixed weight" in x.lower() for x in errs), errs
    assert any("Ops" in x and "no fixed weight" in x.lower() for x in errs), errs
    assert not any("Sales" in x for x in errs)


def test_fixed_values_refuses_a_window_that_starts_inside_the_period():
    rows = [_row("Sales", 1, date(2025, 1, 1), date(2026, 1, 14)),
            _row("Sales", 2, date(2026, 1, 15))]
    with raises(M.CostAllocationError) as e:
        M.fixed_values(rows, ["Sales"], *JAN)
    errs = e.value.errors
    assert any("Sales" in x and "inside the period" in x for x in errs), errs
    assert any("2026-01-15" in x for x in errs), errs


def test_fixed_values_refuses_a_window_that_ends_inside_the_period_even_if_another_covers():
    rows = [_row("Sales", 1, date(2025, 1, 1)),                      # covers
            _row("Sales", 9, date(2025, 6, 1), date(2026, 1, 20))]   # ends inside: a change
    with raises(M.CostAllocationError) as e:
        M.fixed_values(rows, ["Sales"], *JAN)
    assert any("Sales" in x and "inside the period" in x and "2026-01-20" in x
               for x in e.value.errors), e.value.errors


def test_fixed_values_refuses_two_covering_windows_for_one_member():
    rows = [_row("Sales", 1, date(2025, 1, 1)),
            _row("Sales", 2, date(2025, 6, 1), date(2026, 12, 31))]
    with raises(M.CostAllocationError) as e:
        M.fixed_values(rows, ["Sales"], *JAN)
    assert any("Sales" in x and "more than one" in x for x in e.value.errors), e.value.errors


def test_fixed_values_lists_every_problem_once_receivers_sorted():
    rows = [_row("Ops", 1, date(2026, 1, 10)),   # starts inside
            _row("Zeta", 1, date(2025, 1, 1)), _row("Zeta", 1, date(2025, 1, 1))]  # two cover
    with raises(M.CostAllocationError) as e:
        M.fixed_values(rows, ["Zeta", "Ops", "Alpha"], *JAN)
    errs = e.value.errors
    members = [next(m for m in ("Alpha", "Ops", "Zeta") if m in x) for x in errs]
    assert members == sorted(members), errs
    assert "Alpha" in errs[0] and "Zeta" in errs[-1]
    assert len(errs) == 3, errs


def test_fixed_values_ignores_rows_of_members_that_do_not_receive():
    rows = [_row("Sales", 1, date(2026, 1, 1)), _row("Other", 7, date(2026, 1, 10))]
    assert M.fixed_values(rows, ["Sales"], *JAN) == {"Sales": Fraction(1)}


def test_fixed_values_refuses_a_period_that_ends_before_it_starts():
    with raises(M.CostAllocationError):
        M.fixed_values([], ["Sales"], date(2026, 1, 31), date(2026, 1, 1))


def test_fixed_windows_returns_the_row_used():
    chosen = _row("Sales", 5, date(2026, 1, 1))
    rows = [_row("Sales", 3, date(2025, 1, 1), date(2025, 12, 31)), chosen]
    assert M.fixed_windows(rows, ["Sales"], *JAN) == {"Sales": chosen}


# --- derived_values -----------------------------------------------------------

def test_derived_values_as_booked():
    sums = {"Sales": (Decimal("120.50"), 3), "Ops": (Decimal("0"), 2)}
    out = M.derived_values(sums, ["Sales", "Ops"], M.AS_BOOKED)
    assert out == {"Sales": Fraction("120.50"), "Ops": Fraction(0)}
    assert all(isinstance(v, Fraction) for v in out.values())


def test_derived_values_reversed_negates():
    sums = {"Sales": (Decimal("-120.50"), 3), "Ops": (Decimal("-10"), 1)}
    assert M.derived_values(sums, ["Sales", "Ops"], M.REVERSED) == {
        "Sales": Fraction("120.50"), "Ops": Fraction(10)}


def test_derived_values_zero_with_rows_is_a_real_zero_but_no_rows_is_missing():
    sums = {"Sales": (Decimal("0"), 4), "Ops": (Decimal("0"), 0)}
    with raises(M.CostAllocationError) as e:
        M.derived_values(sums, ["Sales", "Ops"], M.AS_BOOKED)
    errs = e.value.errors
    assert any("Ops" in x and "missing" in x.lower() for x in errs), errs
    assert not any("Sales" in x for x in errs)
    assert M.derived_values({"Sales": (0, 4)}, ["Sales"], M.AS_BOOKED) == {"Sales": Fraction(0)}


def test_derived_values_lists_every_missing_receiver_sorted():
    with raises(M.CostAllocationError) as e:
        M.derived_values({"Ops": (5, 1)}, ["Zeta", "Ops", "Alpha"], M.AS_BOOKED)
    errs = e.value.errors
    assert len(errs) == 1, errs
    assert errs[0].index("Alpha") < errs[0].index("Zeta")
    assert "Ops" not in errs[0]


def test_derived_values_refuses_a_negative_value_after_the_sign():
    sums = {"Sales": (Decimal("-5.25"), 2), "Ops": (Decimal("7"), 2)}
    with raises(M.CostAllocationError) as e:
        M.derived_values(sums, ["Sales", "Ops"], M.AS_BOOKED)
    errs = e.value.errors
    assert any("Sales" in x and "-5.25" in x and "negative" in x for x in errs), errs
    assert not any("Ops" in x for x in errs)
    # the same figures, Reversed: Ops is now the negative one
    with raises(M.CostAllocationError) as e:
        M.derived_values(sums, ["Sales", "Ops"], M.REVERSED)
    errs = e.value.errors
    assert any("Ops" in x and "-7" in x for x in errs), errs
    assert not any("Sales" in x for x in errs)


def test_derived_values_reports_missing_and_negative_together():
    sums = {"Sales": (Decimal("-1"), 1)}
    with raises(M.CostAllocationError) as e:
        M.derived_values(sums, ["Sales", "Ops"], M.AS_BOOKED)
    errs = e.value.errors
    assert any("Ops" in x for x in errs) and any("Sales" in x for x in errs), errs


def test_derived_values_refuses_an_unknown_sign():
    with raises(M.CostAllocationError) as e:
        M.derived_values({"Sales": (1, 1)}, ["Sales"], "Absolute")
    assert any("As Booked" in x and "Reversed" in x for x in e.value.errors), e.value.errors


def test_value_signs_constants():
    assert M.VALUE_SIGNS == ("As Booked", "Reversed")
    assert M.AS_BOOKED == "As Booked" and M.REVERSED == "Reversed"


# --- derived_query ------------------------------------------------------------

def _q(**kw):
    base = dict(entity="ZZE", scenario="ZZ-BUD", fiscal_year=2026, fiscal_period=1,
                dimension_column="dim_department", members=["Sales", "Ops"],
                accounts=["5000", "5010", "5011"])
    base.update(kw)
    return M.derived_query(**base)


def test_derived_query_sql_shape():
    sql, params = _q()
    assert sql.startswith("SELECT `dim_department` AS member, sum(amount) AS total, count() AS row_count ")
    assert "FROM epm_gold.gold_scenario_trial_balance " in sql
    assert "scenario_id = {scenario:String}" in sql
    assert "data_area_id = {entity:String}" in sql
    assert "fiscal_year = {fiscal_year:UInt16}" in sql
    assert "fiscal_period = {fiscal_period:UInt8}" in sql
    assert "main_account IN {accounts:Array(String)}" in sql
    assert "`dim_department` IN {members:Array(String)}" in sql
    assert sql.endswith("GROUP BY `dim_department` ORDER BY member FORMAT TabSeparated")
    # no value is interpolated: only the validated identifier appears literally
    for v in ("ZZE", "ZZ-BUD", "2026", "Sales", "5000"):
        assert v not in sql, (v, sql)


def test_derived_query_params_are_clickhouse_http_parameters():
    _, params = _q()
    assert params == {
        "param_scenario": "ZZ-BUD", "param_entity": "ZZE",
        "param_fiscal_year": "2026", "param_fiscal_period": "1",
        "param_members": "['Sales','Ops']",
        "param_accounts": "['5000','5010','5011']",
    }


def test_derived_query_escapes_quotes_and_backslashes_in_array_values():
    _, params = _q(members=["O'Brien", "a\\b"])
    assert params["param_members"] == "['O\\'Brien','a\\\\b']"


def test_derived_query_refuses_a_dimension_column_that_is_not_dim_identifier():
    for bad in ("Department", "dim_Department", "dim_x; DROP TABLE t", "dim_x`", "dim_",
                "dim_x\n", "", None, "main_account"):
        with raises(M.CostAllocationError) as e:
            _q(dimension_column=bad)
        assert any("dimension column" in x.lower() for x in e.value.errors), (bad, e.value.errors)


def test_derived_query_refuses_no_members_or_no_accounts():
    with raises(M.CostAllocationError) as e:
        _q(members=[])
    assert any("member" in x.lower() for x in e.value.errors), e.value.errors
    with raises(M.CostAllocationError) as e:
        _q(accounts=[])
    assert any("account" in x.lower() for x in e.value.errors), e.value.errors


def test_derived_query_docstring_says_accounts_are_already_expanded():
    assert "descendant" in M.derived_query.__doc__.lower()

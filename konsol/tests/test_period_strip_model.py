"""Period strips rule, pure: konsol/close/period_strip_model.py. No frappe."""
import importlib.util
import os

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load():
    spec = importlib.util.spec_from_file_location(
        "period_strip_model_under_test", os.path.join(APP_DIR, "close", "period_strip_model.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


M = _load()


def _p(fy, fp, period_type="Regular"):
    return {"fiscal_year": fy, "fiscal_period": fp, "period_type": period_type}


def test_bounds_are_fixed():
    info = M.strip_info([], None)
    assert (info["year_min"], info["year_max"], info["period_min"], info["period_max"]) == (
        1980, 2045, 1, 12)
    assert info["years"] == [] and info["latest"] is None


def test_years_sorted_unique():
    rows = [_p(2025, 2), _p(2024, 1), _p(2025, 1), _p(2024, 2)]
    assert M.strip_info(rows, None)["years"] == [2024, 2025]


def test_latest_with_data_wins_when_declared():
    rows = [_p(2024, p) for p in range(1, 13)] + [_p(2025, p) for p in range(1, 13)]
    info = M.strip_info(rows, (2024, 7))
    assert info["latest"] == {"fiscal_year": 2024, "fiscal_period": 7}


def test_latest_with_data_undeclared_falls_back_to_latest_regular():
    rows = [_p(2025, 1), _p(2025, 2), _p(2025, 13, "Adjustment")]
    info = M.strip_info(rows, (2030, 1))
    assert info["latest"] == {"fiscal_year": 2025, "fiscal_period": 2}


def test_no_data_uses_latest_regular_skipping_non_regular():
    rows = [_p(2024, 12), _p(2025, 13, "Adjustment")]
    assert M.strip_info(rows, None)["latest"] == {"fiscal_year": 2024, "fiscal_period": 12}


def test_latest_with_data_may_be_a_non_regular_declared_period():
    rows = [_p(2025, 12), _p(2025, 13, "Adjustment")]
    assert M.strip_info(rows, (2025, 13))["latest"] == {"fiscal_year": 2025, "fiscal_period": 13}


def test_no_declared_periods_means_none():
    assert M.strip_info([], (2025, 1))["latest"] is None
    assert M.strip_info([_p(2025, 13, "Adjustment")], None)["latest"] is None

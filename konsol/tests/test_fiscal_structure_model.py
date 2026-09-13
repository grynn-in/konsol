"""Fiscal year period-row rules (konsol#189), pure: konsol/fiscal_structure_model.py.

Loaded by path; the module imports nothing from frappe or konsol.
"""
import importlib.util
import os
from datetime import date

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_spec = importlib.util.spec_from_file_location(
    "fsm_under_test", os.path.join(APP_DIR, "fiscal_structure_model.py"))
M = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(M)


def row(period, code):
    return {"period": period, "code": code}


def test_duplicate_number_and_code_named():
    rows = [row(1, "P01"), row(3, "P03"), row(4, "P04"), row(5, "P05"), row(3, "P03")]
    errors = M.period_problems(rows)
    assert any("Period 3 is used by rows 2 and 5" in e for e in errors), errors
    assert any("Code 'P03' is used by rows 2 and 5" in e for e in errors), errors
    # case-insensitive code match
    assert any("used by rows" in e for e in M.period_problems([row(1, "p01"), row(2, "P01")]))


def test_period_number_range():
    assert M.period_problems([row(-1, "P01")])
    assert any("Row 1: period -1 is outside 0..255" in e for e in M.period_problems([row(-1, "P01")]))
    assert M.period_problems([row(256, "P01")])
    assert any("Row 1: period 256 is outside 0..255" in e for e in M.period_problems([row(256, "P01")]))
    assert M.period_problems([row(0, "P01")]) == []
    assert M.period_problems([row(255, "P01")]) == []


def test_blank_code_refused():
    assert M.period_problems([row(1, "")])
    assert M.period_problems([row(1, "   ")])


def fy(year, start_date, end_date):
    return {"year": year, "start_date": start_date, "end_date": end_date}


def fy_row(start_date, end_date, code="P01"):
    return {"start_date": start_date, "end_date": end_date, "code": code}


def test_year_range_and_dates():
    # year integer bounds: 1969 and 2149 refused, 1970 and 2148 accepted
    assert M.year_problems(fy(1969, date(1969, 1, 1), date(1969, 12, 31)), [])
    assert M.year_problems(fy(2149, date(2149, 1, 1), date(2149, 6, 6)), [])
    assert M.year_problems(fy(1970, date(1970, 1, 1), date(1970, 12, 31)), []) == []
    assert M.year_problems(fy(2148, date(2148, 1, 1), date(2148, 12, 31)), []) == []

    # start_date must be before end_date
    assert M.year_problems(fy(2020, date(2020, 12, 31), date(2020, 12, 31)), [])
    assert M.year_problems(fy(2020, date(2020, 12, 31), date(2020, 1, 1)), [])

    # any date past 2149-06-06 (max ClickHouse Date) is refused
    assert M.year_problems(fy(2149, date(2149, 1, 1), date(2149, 6, 7)), [])


def test_row_outside_year_named():
    year = fy(2020, date(2020, 1, 1), date(2020, 12, 31))

    # row starting before the year, naming the row (1-based position and code)
    errors = M.year_problems(year, [fy_row(date(2019, 12, 31), date(2020, 6, 30), code="P01")])
    assert errors
    assert any("Row 1" in e and "P01" in e for e in errors), errors

    # row ending after the year, naming the row
    errors = M.year_problems(year, [fy_row(date(2020, 1, 1), date(2021, 1, 1), code="P02")])
    assert errors
    assert any("Row 1" in e and "P02" in e for e in errors), errors

    # a row with start > end is refused regardless of the year bounds
    errors = M.year_problems(year, [fy_row(date(2020, 6, 30), date(2020, 1, 1), code="P03")])
    assert errors
    assert any("Row 1" in e and "P03" in e for e in errors), errors

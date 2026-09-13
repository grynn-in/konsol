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


def reg(period, start_date, end_date, type_="Regular"):
    return {"period": period, "code": f"P{period:02d}", "type": type_,
            "start_date": start_date, "end_date": end_date}


def test_regular_periods_tile_the_year():
    import calendar
    year = fy(2025, date(2025, 1, 1), date(2025, 12, 31))

    def months():
        return [reg(m, date(2025, m, 1), date(2025, m, calendar.monthrange(2025, m)[1]))
                for m in range(1, 13)]

    # a valid monthly calendar year, plus ignored Opening/Closing rows, passes
    valid = months() + [reg(0, date(2025, 1, 1), date(2025, 1, 1), "Opening"),
                        reg(13, date(2025, 12, 31), date(2025, 12, 31), "Closing")]
    assert M.regular_period_problems(year, valid) == []

    # a gap: P03 starts 3 days late
    rows = months()
    rows[2]["start_date"] = date(2025, 3, 4)
    errors = M.regular_period_problems(year, rows)
    assert any("P03 starts 2025-03-04 but P02 ends 2025-02-28: a gap of 3 days" in e for e in errors), errors

    # an overlap: P04 starts inside P03
    rows = months()
    rows[3]["start_date"] = date(2025, 3, 30)
    errors = M.regular_period_problems(year, rows)
    assert any("P04 overlaps P03" in e for e in errors), errors

    # a late first start
    rows = months()
    rows[0]["start_date"] = date(2025, 1, 2)
    errors = M.regular_period_problems(year, rows)
    assert any("The first Regular period P01 starts" in e and "after the year start 2025-01-01" in e
               for e in errors), errors

    # an early last end
    rows = months()
    rows[11]["end_date"] = date(2025, 12, 30)
    errors = M.regular_period_problems(year, rows)
    assert any("The last Regular period P12 ends" in e and "before the year end 2025-12-31" in e
               for e in errors), errors

    # a numbering gap: 1, 2, 4
    rows = [reg(1, date(2025, 1, 1), date(2025, 4, 30)),
            reg(2, date(2025, 5, 1), date(2025, 8, 31)),
            reg(4, date(2025, 9, 1), date(2025, 12, 31))]
    errors = M.regular_period_problems(year, rows)
    assert any("Regular periods are numbered 1, 2, 4: 3 is missing" in e for e in errors), errors

    # no Regular rows at all (only other types)
    errors = M.regular_period_problems(year, [reg(0, date(2025, 1, 1), date(2025, 12, 31), "Opening")])
    assert any("No Regular periods" in e for e in errors), errors
    assert M.regular_period_problems(year, [])


def opn(period, start, end, code="OPN"):
    return {"period": period, "code": code, "type": "Opening", "start_date": start, "end_date": end}


def cls(period, start, end, code="CLS"):
    return {"period": period, "code": code, "type": "Closing", "start_date": start, "end_date": end}


def adj(period, start, end, code="ADJ"):
    return {"period": period, "code": code, "type": "Adjustment", "start_date": start, "end_date": end}


def _monthly_year():
    import calendar
    year = fy(2025, date(2025, 1, 1), date(2025, 12, 31))
    months = [reg(m, date(2025, m, 1), date(2025, m, calendar.monthrange(2025, m)[1]))
              for m in range(1, 13)]
    return year, months


def test_opening_closing_adjustment_placement():
    year, months = _monthly_year()

    # OPN 0, P01..P12, CLS 13, ADJ 14 - a valid year
    valid_rows = ([opn(0, date(2025, 1, 1), date(2025, 1, 1))] + months +
                  [cls(13, date(2025, 12, 31), date(2025, 12, 31)),
                   adj(14, date(2025, 6, 1), date(2025, 6, 30))])
    assert M.placement_problems(year, valid_rows) == []

    # two Opening rows
    rows = valid_rows + [opn(15, date(2025, 1, 1), date(2025, 1, 1), code="OPN2")]
    errors = M.placement_problems(year, rows)
    assert any("Only one Opening" in e and "OPN" in e and "OPN2" in e for e in errors), errors

    # Opening not period 0
    rows = [opn(1, date(2025, 1, 1), date(2025, 1, 1))] + months + [cls(13, date(2025, 12, 31), date(2025, 12, 31))]
    errors = M.placement_problems(year, rows)
    assert any("OPN" in e and "period 0" in e for e in errors), errors

    # a Regular period using period 0 (0 is reserved for Opening)
    rows = valid_rows + [reg(0, date(2025, 1, 1), date(2025, 1, 31))]
    errors = M.placement_problems(year, rows)
    assert any("Period 0 is reserved for Opening" in e and "P00" in e for e in errors), errors

    # Opening not on the year start day
    rows = [opn(0, date(2025, 1, 2), date(2025, 1, 2))] + months + [cls(13, date(2025, 12, 31), date(2025, 12, 31))]
    errors = M.placement_problems(year, rows)
    assert any("OPN" in e and "one day" in e for e in errors), errors

    # Opening longer than one day
    rows = [opn(0, date(2025, 1, 1), date(2025, 1, 2))] + months + [cls(13, date(2025, 12, 31), date(2025, 12, 31))]
    errors = M.placement_problems(year, rows)
    assert any("OPN" in e and "one day" in e for e in errors), errors

    # Closing below a Regular period
    rows = [opn(0, date(2025, 1, 1), date(2025, 1, 1))] + months + [cls(5, date(2025, 12, 31), date(2025, 12, 31))]
    errors = M.placement_problems(year, rows)
    assert any("CLS" in e and "above every Regular period" in e for e in errors), errors

    # Closing not on the year end day
    rows = [opn(0, date(2025, 1, 1), date(2025, 1, 1))] + months + [cls(13, date(2025, 12, 30), date(2025, 12, 31))]
    errors = M.placement_problems(year, rows)
    assert any("CLS" in e and "one day" in e for e in errors), errors

    # two Closing rows
    rows = valid_rows + [cls(16, date(2025, 12, 31), date(2025, 12, 31), code="CLS2")]
    errors = M.placement_problems(year, rows)
    assert any("Only one Closing" in e and "CLS" in e and "CLS2" in e for e in errors), errors

    # Adjustment numbered inside the Regular range
    rows = [opn(0, date(2025, 1, 1), date(2025, 1, 1))] + months + [
        cls(13, date(2025, 12, 31), date(2025, 12, 31)), adj(5, date(2025, 6, 1), date(2025, 6, 30))]
    errors = M.placement_problems(year, rows)
    assert any("ADJ" in e and "above every Regular period" in e for e in errors), errors

    # Adjustment outside the year
    rows = [opn(0, date(2025, 1, 1), date(2025, 1, 1))] + months + [
        cls(13, date(2025, 12, 31), date(2025, 12, 31)), adj(14, date(2024, 12, 31), date(2025, 1, 5))]
    errors = M.placement_problems(year, rows)
    assert any("ADJ" in e and "before the year start" in e for e in errors), errors

    # unknown type is refused, naming it
    rows = valid_rows + [{"period": 20, "code": "XYZ", "type": "Bogus",
                           "start_date": date(2025, 6, 1), "end_date": date(2025, 6, 1)}]
    errors = M.placement_problems(year, rows)
    assert any("XYZ" in e and "Bogus" in e for e in errors), errors

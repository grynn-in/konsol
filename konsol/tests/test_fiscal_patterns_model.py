"""Monthly (12) fiscal period generation, pure: konsol/fiscal_patterns_model.py.

Loaded by path; the module imports nothing from frappe or konsol.
"""
import datetime
import importlib.util
import os

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_spec = importlib.util.spec_from_file_location(
    "fpm_under_test", os.path.join(APP_DIR, "fiscal_patterns_model.py"))
M = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(M)


def d(y, m, day):
    return datetime.date(y, m, day)


def test_monthly_calendar_year():
    rows = M.monthly_periods(d(2025, 1, 1), d(2025, 12, 31))
    assert len(rows) == 12

    jan = rows[0]
    assert jan["period"] == 1
    assert jan["code"] == "P01"
    assert jan["label"] == "Jan 2025"
    assert jan["type"] == "Regular"
    assert jan["start_date"] == d(2025, 1, 1)
    assert jan["end_date"] == d(2025, 1, 31)
    assert jan["quarter"] == "Q1"

    for i, row in enumerate(rows, start=1):
        assert row["period"] == i
        assert row["code"] == "P%02d" % i

    assert rows[11]["code"] == "P12"
    assert rows[11]["label"] == "Dec 2025"
    assert rows[11]["start_date"] == d(2025, 12, 1)
    assert rows[11]["end_date"] == d(2025, 12, 31)
    assert rows[11]["quarter"] == "Q4"

    # contiguity: each period ends the day before the next starts
    for prev, nxt in zip(rows, rows[1:]):
        assert prev["end_date"] + datetime.timedelta(days=1) == nxt["start_date"]

    quarters = [row["quarter"] for row in rows]
    assert quarters == ["Q1"] * 3 + ["Q2"] * 3 + ["Q3"] * 3 + ["Q4"] * 3


def test_monthly_mid_month_start():
    rows = M.monthly_periods(d(2025, 4, 6), d(2026, 4, 5))
    assert len(rows) == 12

    p1 = rows[0]
    assert p1["start_date"] == d(2025, 4, 6)
    assert p1["end_date"] == d(2025, 5, 5)
    assert p1["label"] == "Apr 2025"
    assert p1["quarter"] == "Q1"

    p12 = rows[11]
    assert p12["code"] == "P12"
    assert p12["start_date"] == d(2026, 3, 6)
    assert p12["end_date"] == d(2026, 4, 5)
    assert p12["label"] == "Mar 2026"
    assert p12["quarter"] == "Q4"

    for row in rows:
        assert row["start_date"].day == 6

    for prev, nxt in zip(rows, rows[1:]):
        assert prev["end_date"] + datetime.timedelta(days=1) == nxt["start_date"]


def test_monthly_refuses_wrong_length():
    try:
        M.monthly_periods(d(2025, 1, 1), d(2026, 1, 31))
    except ValueError as e:
        assert "13" in str(e), str(e)
    else:
        raise AssertionError("expected ValueError for a 13-month span")


def test_thirteen_periods_364_and_371():
    start = d(2025, 1, 1)

    # 364-day year: 13 periods of 28 days each.
    end_364 = start + datetime.timedelta(days=363)
    rows = M.thirteen_periods(start, end_364)
    assert len(rows) == 13

    p1 = rows[0]
    assert p1["period"] == 1
    assert p1["code"] == "P01"
    assert p1["label"] == "P01 2025"
    assert p1["type"] == "Regular"
    assert p1["start_date"] == start
    assert p1["end_date"] == start + datetime.timedelta(days=27)
    assert p1["quarter"] == ""

    for i, row in enumerate(rows, start=1):
        assert row["period"] == i
        assert row["code"] == "P%02d" % i
        assert row["label"] == "P%02d 2025" % i
        assert row["quarter"] == ""
        length = (row["end_date"] - row["start_date"]).days + 1
        assert length == 28, (i, length)

    assert rows[12]["code"] == "P13"
    assert rows[12]["end_date"] == end_364

    for prev, nxt in zip(rows, rows[1:]):
        assert prev["end_date"] + datetime.timedelta(days=1) == nxt["start_date"]

    # 371-day year (53 weeks): P01..P12 stay 28 days, P13 stretches to 35.
    end_371 = start + datetime.timedelta(days=370)
    rows = M.thirteen_periods(start, end_371)
    assert len(rows) == 13

    for row in rows[:12]:
        length = (row["end_date"] - row["start_date"]).days + 1
        assert length == 28, row

    p13 = rows[12]
    assert p13["code"] == "P13"
    assert p13["label"] == "P13 2025"
    assert p13["quarter"] == ""
    assert p13["end_date"] == end_371
    assert (p13["end_date"] - p13["start_date"]).days + 1 == 35

    for prev, nxt in zip(rows, rows[1:]):
        assert prev["end_date"] + datetime.timedelta(days=1) == nxt["start_date"]


def test_thirteen_refuses_365():
    start = d(2025, 1, 1)
    end = start + datetime.timedelta(days=364)  # 365 days total
    try:
        M.thirteen_periods(start, end)
    except ValueError as e:
        assert "365" in str(e), str(e)
    else:
        raise AssertionError("expected ValueError for a 365-day span")


def test_445_weeks_and_quarters():
    start = d(2025, 1, 1)
    expected_lengths_364 = [28, 28, 35] * 4

    # 364-day year: 4-4-5 weeks (28, 28, 35 days) repeated four times.
    end_364 = start + datetime.timedelta(days=363)
    rows = M.four_four_five_periods(start, end_364)
    assert len(rows) == 12

    p1 = rows[0]
    assert p1["period"] == 1
    assert p1["code"] == "P01"
    assert p1["label"] == "P01 2025"
    assert p1["type"] == "Regular"
    assert p1["start_date"] == start
    assert p1["end_date"] == start + datetime.timedelta(days=27)
    assert p1["quarter"] == "Q1"

    for i, row in enumerate(rows, start=1):
        assert row["period"] == i
        assert row["code"] == "P%02d" % i
        assert row["label"] == "P%02d 2025" % i
        length = (row["end_date"] - row["start_date"]).days + 1
        assert length == expected_lengths_364[i - 1], (i, length)

    assert rows[11]["code"] == "P12"
    assert rows[11]["end_date"] == end_364

    quarters = [row["quarter"] for row in rows]
    assert quarters == ["Q1"] * 3 + ["Q2"] * 3 + ["Q3"] * 3 + ["Q4"] * 3

    for prev, nxt in zip(rows, rows[1:]):
        assert prev["end_date"] + datetime.timedelta(days=1) == nxt["start_date"]

    # 371-day year (53 weeks): same, but P12 stretches to 6 weeks (42 days).
    end_371 = start + datetime.timedelta(days=370)
    rows = M.four_four_five_periods(start, end_371)
    assert len(rows) == 12

    expected_lengths_371 = [28, 28, 35] * 3 + [28, 28, 42]
    for i, row in enumerate(rows, start=1):
        length = (row["end_date"] - row["start_date"]).days + 1
        assert length == expected_lengths_371[i - 1], (i, length)

    p12 = rows[11]
    assert p12["code"] == "P12"
    assert p12["label"] == "P12 2025"
    assert p12["quarter"] == "Q4"
    assert p12["end_date"] == end_371
    assert (p12["end_date"] - p12["start_date"]).days + 1 == 42

    quarters = [row["quarter"] for row in rows]
    assert quarters == ["Q1"] * 3 + ["Q2"] * 3 + ["Q3"] * 3 + ["Q4"] * 3

    for prev, nxt in zip(rows, rows[1:]):
        assert prev["end_date"] + datetime.timedelta(days=1) == nxt["start_date"]


def test_445_refuses_365():
    start = d(2025, 1, 1)
    end = start + datetime.timedelta(days=364)  # 365 days total
    try:
        M.four_four_five_periods(start, end)
    except ValueError as e:
        assert "365" in str(e), str(e)
    else:
        raise AssertionError("expected ValueError for a 365-day span")

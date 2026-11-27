"""Forecast horizon coverage, the pure rule (konsol#348 S6 KA8; QB10 (b),
S6-Q9 (a): a Desk report over the Cycles and the declared calendar).

A forecast round is (scenario, as-of date) (Q23 a); a horizon crossing the
year end is one Cycle per fiscal year sharing the as-of date (Q24 a). The
round covers the ``horizon_periods`` Regular periods after its as-of period,
counted in DECLARED periods (BUD-10-1), across years. A year inside that span
with no approved Cycle of the round is uncovered: the report names the
periods and the draft Cycles, so the horizon is never silently shorter than
declared. Three states, each with a sentence:

- ``ok``: every period of the horizon has an approved Cycle for its year;
- ``uncovered``: some period has none (a draft, a missing Cycle, or a
  calendar that declares no Regular period that far);
- ``undeclared_as_of``: the as-of date falls in no declared Regular period,
  so the horizon cannot be counted.

Pure: no frappe, no ClickHouse. The caller passes the Budget Cycle rows and
``fiscal_calendar.fiscal_period_rows()``.
"""
from datetime import date

from konsol.epm import horizon_coverage_model as M


def _calendar(years):
    rows = []
    for fy in years:
        for fp in range(1, 13):
            start = date(fy, fp, 1)
            next_first = date(fy + (fp == 12), (fp % 12) + 1, 1)
            end = date.fromordinal(next_first.toordinal() - 1)
            rows.append({"fiscal_year": fy, "fiscal_period": fp, "period_type": "Regular",
                         "start_date": start, "end_date": end, "status": "Open"})
        rows.append({"fiscal_year": fy, "fiscal_period": 0, "period_type": "Opening",
                     "start_date": date(fy, 1, 1), "end_date": date(fy, 1, 1), "status": "Open"})
        rows.append({"fiscal_year": fy, "fiscal_period": 13, "period_type": "Closing",
                     "start_date": date(fy, 12, 31), "end_date": date(fy, 12, 31), "status": "Open"})
    return rows


def _cycle(name, fiscal_year, as_of, horizon, docstatus=1, scenario_id="ZZFC", scenario_type="forecast"):
    return {"name": name, "scenario_id": scenario_id, "scenario_type": scenario_type, "fiscal_year": fiscal_year,
            "as_of_date": as_of, "horizon_periods": horizon, "docstatus": docstatus}


def test_a_round_crossing_the_year_end_with_both_years_approved_is_ok():
    """12 periods from P10 FY2027: P11-P12 of FY2027 and P1-P10 of FY2028, two
    approved Cycles sharing the as-of date (Q24 a)."""
    cycles = [_cycle("FC-27", 2027, date(2027, 10, 31), 12), _cycle("FC-28", 2028, date(2027, 10, 31), 12)]
    [r] = M.rounds(cycles, _calendar([2027, 2028]))
    assert r["state"] == M.OK
    assert r["scenario_id"] == "ZZFC" and r["as_of_date"] == "2027-10-31"
    assert r["as_of_period"] == (2027, 10)
    assert r["horizon_periods"] == 12
    assert r["periods"] == [(2027, 11), (2027, 12)] + [(2028, p) for p in range(1, 11)]
    assert r["uncovered"] == [] and r["undeclared_periods"] == 0
    assert r["cycles"] == ["FC-27", "FC-28"] and r["approved_years"] == [2027, 2028] and r["draft_cycles"] == []
    assert r["sentence"] == ("Round ZZFC as of 2027-10-31 covers FY2027 P11-P12, FY2028 P1-P10 (12 periods): "
                             "approved for FY2027, FY2028 (FC-27, FC-28).")


def test_a_draft_second_year_leaves_its_periods_uncovered_and_names_the_draft():
    cycles = [_cycle("FC-27", 2027, date(2027, 10, 31), 12), _cycle("FC-28", 2028, date(2027, 10, 31), 12, docstatus=0)]
    [r] = M.rounds(cycles, _calendar([2027, 2028]))
    assert r["state"] == M.UNCOVERED
    assert r["uncovered"] == [(2028, p) for p in range(1, 11)]
    assert r["approved_years"] == [2027] and r["draft_cycles"] == ["FC-28"]
    assert "FY2028 P1-P10" in r["sentence"] and "FC-28" in r["sentence"] and "draft" in r["sentence"]
    assert r["sentence"].startswith("Round ZZFC as of 2027-10-31 covers FY2027 P11-P12, FY2028 P1-P10 (12 periods): "
                                    "uncovered FY2028 P1-P10")


def test_a_missing_second_year_cycle_is_uncovered_with_no_draft_to_name():
    [r] = M.rounds([_cycle("FC-27", 2027, date(2027, 10, 31), 12)], _calendar([2027, 2028]))
    assert r["state"] == M.UNCOVERED
    assert r["uncovered"] == [(2028, p) for p in range(1, 11)]
    assert r["draft_cycles"] == []
    assert "no Cycle of the round for FY2028" in r["sentence"]


def test_an_as_of_date_in_no_declared_regular_period_is_undeclared():
    """The calendar declares FY2027 only; an as-of in 2029 is in no Regular
    period, so the horizon cannot be counted (BUD-10-1 counts declared periods)."""
    [r] = M.rounds([_cycle("FC-29", 2029, date(2029, 3, 31), 6)], _calendar([2027]))
    assert r["state"] == M.UNDECLARED_AS_OF
    assert r["as_of_period"] is None and r["periods"] == [] and r["uncovered"] == []
    assert r["sentence"] == ("Round ZZFC as of 2029-03-31: the as-of date is in no declared Regular period, so "
                             "its horizon of 6 periods cannot be counted. Declare the period in EPM Fiscal Year.")


def test_an_as_of_in_an_opening_or_closing_period_only_is_undeclared():
    """Only a Regular period holds an as-of (the deal_period_holds rule); a
    calendar whose Regular periods stop before the date is undeclared too."""
    rows = [r for r in _calendar([2027]) if r["period_type"] != "Regular" or r["fiscal_period"] < 12]
    [r] = M.rounds([_cycle("FC-27", 2027, date(2027, 12, 31), 3)], rows)
    assert r["state"] == M.UNDECLARED_AS_OF


def test_a_horizon_past_the_declared_calendar_is_uncovered_naming_the_gap():
    """FY2028 is not declared: of the 12 periods after P10 FY2027 only P11 and
    P12 exist in the calendar, so 10 are undeclared. The horizon is never
    silently shortened to what the calendar has."""
    [r] = M.rounds([_cycle("FC-27", 2027, date(2027, 10, 31), 12)], _calendar([2027]))
    assert r["state"] == M.UNCOVERED
    assert r["periods"] == [(2027, 11), (2027, 12)]
    assert r["undeclared_periods"] == 10 and r["uncovered"] == []
    assert "10 of its 12 periods are after the last declared Regular period (FY2027 P12)" in r["sentence"]


def test_a_short_horizon_inside_one_year_is_ok_with_one_cycle():
    [r] = M.rounds([_cycle("FC-27", 2027, date(2027, 6, 30), 6)], _calendar([2027]))
    assert r["state"] == M.OK
    assert r["periods"] == [(2027, p) for p in range(7, 13)]
    assert r["sentence"] == "Round ZZFC as of 2027-06-30 covers FY2027 P7-P12 (6 periods): approved for FY2027 (FC-27)."


def test_rounds_are_grouped_by_scenario_and_as_of_and_ordered():
    cycles = [_cycle("B-27", 2027, date(2027, 9, 30), 3, scenario_id="ZZFC2"),
              _cycle("FC-27-Q2", 2027, date(2027, 6, 30), 6),
              _cycle("FC-27-Q1", 2027, date(2027, 3, 31), 9),
              _cycle("FC-28-Q1", 2028, date(2027, 3, 31), 9)]
    got = M.rounds(cycles, _calendar([2027, 2028]))
    assert [(r["scenario_id"], r["as_of_date"], r["cycles"]) for r in got] == [
        ("ZZFC", "2027-03-31", ["FC-27-Q1", "FC-28-Q1"]),
        ("ZZFC", "2027-06-30", ["FC-27-Q2"]),
        ("ZZFC2", "2027-09-30", ["B-27"]),
    ]


def test_cancelled_cycles_and_non_forecast_cycles_are_not_rounds():
    cycles = [_cycle("FC-27-GONE", 2027, date(2027, 3, 31), 9, docstatus=2),
              _cycle("B-27", 2027, None, 0, scenario_id="ZZB", scenario_type="budget"),
              _cycle("FC-27", 2027, date(2027, 6, 30), 6)]
    got = M.rounds(cycles, _calendar([2027]))
    assert [r["cycles"] for r in got] == [["FC-27"]]


def test_a_cancelled_sibling_does_not_cover_its_year():
    cycles = [_cycle("FC-27", 2027, date(2027, 10, 31), 12), _cycle("FC-28", 2028, date(2027, 10, 31), 12, docstatus=2)]
    [r] = M.rounds(cycles, _calendar([2027, 2028]))
    assert r["state"] == M.UNCOVERED and r["cycles"] == ["FC-27"]


def test_an_as_of_date_given_as_a_string_is_the_same_round():
    """Frappe hands a Date field in as 'YYYY-MM-DD'; a date and the string key alike."""
    cycles = [_cycle("FC-27", 2027, "2027-10-31", 12), _cycle("FC-28", 2028, date(2027, 10, 31), 12)]
    [r] = M.rounds(cycles, _calendar([2027, 2028]))
    assert r["state"] == M.OK and r["cycles"] == ["FC-27", "FC-28"]


def test_differing_horizons_in_one_round_take_the_longest_and_say_so():
    """Declared sub-rule: the round's horizon is the longest its Cycles declare;
    a disagreement is named, never averaged or silently taken from one."""
    cycles = [_cycle("FC-27", 2027, date(2027, 10, 31), 12), _cycle("FC-28", 2028, date(2027, 10, 31), 6)]
    [r] = M.rounds(cycles, _calendar([2027, 2028]))
    assert r["horizon_periods"] == 12
    assert "declare different horizons (FC-27: 12, FC-28: 6)" in r["sentence"]


def test_period_range_text_compresses_runs_within_a_year():
    assert M.period_range_text([(2027, 11), (2027, 12), (2028, 1), (2028, 2), (2028, 3)]) == "FY2027 P11-P12, FY2028 P1-P3"
    assert M.period_range_text([(2027, 3)]) == "FY2027 P3"
    assert M.period_range_text([(2027, 1), (2027, 3)]) == "FY2027 P1, FY2027 P3"
    assert M.period_range_text([]) == ""


def test_the_model_imports_nothing_from_frappe():
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(M))
    names = [n.names[0].name for n in ast.walk(tree) if isinstance(n, ast.Import)]
    names += [n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)]
    assert not [n for n in names if n and n.split(".")[0] in ("frappe", "requests")], names


def test_the_horizon_is_the_longest_among_approved_cycles_and_a_longer_draft_is_named():
    """The warehouse cuts a round at the longest horizon among its APPROVED
    Cycles (konsolidat#376, PR #402), so a draft's longer horizon is not the
    round's. It is reported as pending, never dropped."""
    cycles = [_cycle("A26", 2026, date(2026, 9, 30), 12, docstatus=0),
              _cycle("A27", 2027, date(2026, 9, 30), 3, docstatus=1)]
    [r] = M.rounds(cycles, _calendar([2026, 2027]))
    assert r["horizon_periods"] == 3
    assert r["pending_horizon_periods"] == 12
    assert "draft Cycle A26 would extend the horizon to 12 once approved" in r["sentence"]


def test_no_longer_draft_means_no_pending_horizon():
    cycles = [_cycle("A27", 2027, date(2026, 9, 30), 3), _cycle("A26", 2026, date(2026, 9, 30), 3, docstatus=0)]
    [r] = M.rounds(cycles, _calendar([2026, 2027]))
    assert r["pending_horizon_periods"] is None
    assert "would extend" not in r["sentence"]


def test_a_round_with_only_drafts_has_no_horizon_and_is_not_covered():
    [r] = M.rounds([_cycle("A26", 2026, date(2026, 9, 30), 6, docstatus=0)], _calendar([2026, 2027]))
    assert r["horizon_periods"] == 0
    assert r["pending_horizon_periods"] == 6
    assert r["state"] == M.UNCOVERED
    assert "no approved Cycle" in r["sentence"]

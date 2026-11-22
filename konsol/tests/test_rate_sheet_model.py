"""Rate Sheet, the pure rules: konsol/rate_sheet_model.py (konsol#592, pair 1).

Decided by Deepak Pai on 22 Nov 2026: a Statutory sheet per period, a Constant
sheet per year (D1, QA-1, QB-1); a Constant sheet locks when its whole year
closes (P1-Q2 B); no Disabled flag (P1-Q3); year and period are entered and
the dates derived (P1-Q4 A); a correction supersedes (#312-3).

Loaded by path; the module imports nothing from frappe or konsol.
"""
import datetime
import importlib.util
import os

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load(name, *path):
    spec = importlib.util.spec_from_file_location(name, os.path.join(APP_DIR, *path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


M = _load("rsm_under_test", "rate_sheet_model.py")
POLICY = _load("rsm_policy", "close", "close_policy_model.py")


def _cal(year=2025, regular=12, opening=True, closing=True, adjustments=()):
    """A declared year: OPN (0) on 1 Jan like P1, P1..Pn monthly, Adjustment, CLS."""
    rows = []
    if opening:
        rows.append({"fiscal_year": year, "period": 0, "type": "Opening",
                     "start_date": datetime.date(year, 1, 1), "end_date": datetime.date(year, 1, 1)})
    for p in range(1, regular + 1):
        nxt = datetime.date(year + (p == 12), p % 12 + 1, 1)
        rows.append({"fiscal_year": year, "period": p, "type": "Regular",
                     "start_date": datetime.date(year, p, 1), "end_date": nxt - datetime.timedelta(days=1)})
    for a in adjustments:
        rows.append({"fiscal_year": year, "period": a, "type": "Adjustment",
                     "start_date": datetime.date(year, 12, 31), "end_date": datetime.date(year, 12, 31)})
    if closing:
        rows.append({"fiscal_year": year, "period": 99, "type": "Closing",
                     "start_date": datetime.date(year, 12, 31), "end_date": datetime.date(year, 12, 31)})
    return rows


def _srow(f="EUR", t="CHF", avg=0.95, cls=0.96, per=1, **kw):
    return {"from_currency": f, "to_currency": t, "quoted_per": per,
            "average_quote": avg, "closing_quote": cls, **kw}


def _crow(f="EUR", t="CHF", rate=0.94, per=1, **kw):
    return {"from_currency": f, "to_currency": t, "quoted_per": per, "rate": rate, **kw}


def _stat(fy=2025, fp=6, rows=None, **kw):
    return {"name": f"RS-S-{fy}-{fp:02d}", "rate_basis": "Statutory", "fiscal_year": fy,
            "fiscal_period": fp, "rows": [_srow()] if rows is None else rows, "docstatus": 1, **kw}


def _const(fy=2025, rows=None, **kw):
    return {"name": f"RS-C-{fy}", "rate_basis": "Constant", "fiscal_year": fy,
            "fiscal_period": None, "rows": [_crow()] if rows is None else rows, "docstatus": 1, **kw}


# -- T1: window rules --------------------------------------------------------------

def test_window_statutory_regular_period_takes_its_own_dates():
    w, why = M.window(_stat(fp=2), _cal())
    assert why is None
    assert w == (datetime.date(2025, 2, 1), datetime.date(2025, 2, 28))


def test_window_statutory_opening_period_is_allowed():
    w, why = M.window(_stat(fp=0), _cal())
    assert why is None and w == (datetime.date(2025, 1, 1), datetime.date(2025, 1, 1))


def test_window_statutory_closing_period_is_refused_by_name():
    w, why = M.window(_stat(fp=99), _cal())
    assert w is None and "Closing" in why and "FY2025 P99" in why


def test_window_statutory_adjustment_period_takes_its_own_dates():
    """PR #595 review 1: rate_period_map() maps an Adjustment period to itself,
    so the warehouse translates it at its own rates: a Statutory sheet holds them."""
    w, why = M.window(_stat(fp=13), _cal(adjustments=(13,)))
    assert why is None and w == (datetime.date(2025, 12, 31), datetime.date(2025, 12, 31))


# -- PR #595 review 1: the period whose rates a period is translated at -----------

def test_rate_period_reads_a_closing_period_at_the_last_regular_period():
    assert M.rate_period(_cal(adjustments=(13,)), 2025, 99) == 12
    assert M.rate_period(_cal(regular=9), 2025, 99) == 9


def test_rate_period_keeps_every_other_period():
    cal = _cal(adjustments=(13,))
    for fp in (0, 1, 6, 12, 13):
        assert M.rate_period(cal, 2025, fp) == fp


def test_rate_period_keeps_a_closing_period_in_a_year_with_no_regular_period():
    """rate_period_map(): last_regular is 0, so the Closing period maps to itself."""
    cal = [r for r in _cal() if r["type"] == "Closing"]
    assert M.rate_period(cal, 2025, 99) == 99


def test_rate_period_keeps_an_undeclared_period_and_reads_only_its_own_year():
    assert M.rate_period(_cal(), 2025, 50) == 50
    assert M.rate_period(_cal(2024), 2025, 99) == 99
    assert M.rate_period(_cal(2024, regular=3) + _cal(2025), 2025, 99) == 12


def test_window_statutory_needs_a_period():
    for blank in (None, ""):
        w, why = M.window({**_stat(), "fiscal_period": blank}, _cal())
        assert w is None and "needs a Fiscal Period" in why


def test_window_statutory_undeclared_period_or_year_is_refused():
    assert "no period 14" in M.window(_stat(fp=14), _cal())[1]
    assert "not declared" in M.window(_stat(fy=2024), _cal(2025))[1]


def test_window_only_uses_the_sheets_own_year():
    cal = _cal(2024) + _cal(2025)
    w, _ = M.window(_stat(fy=2025, fp=1), cal)
    assert w == (datetime.date(2025, 1, 1), datetime.date(2025, 1, 31))


def test_window_constant_spans_first_start_to_last_end():
    w, why = M.window(_const(), _cal())
    assert why is None and w == (datetime.date(2025, 1, 1), datetime.date(2025, 12, 31))


def test_window_constant_follows_the_calendar_not_the_calendar_year():
    cal = [{"fiscal_year": 2025, "period": 1, "type": "Regular",
            "start_date": datetime.date(2024, 4, 1), "end_date": datetime.date(2024, 4, 30)},
           {"fiscal_year": 2025, "period": 2, "type": "Regular",
            "start_date": datetime.date(2024, 5, 1), "end_date": datetime.date(2025, 3, 31)}]
    assert M.window(_const(), cal)[0] == (datetime.date(2024, 4, 1), datetime.date(2025, 3, 31))


def test_window_constant_has_no_period():
    w, why = M.window(_const(fiscal_period=0), _cal())
    assert w is None and "no Fiscal Period" in why


def test_window_constant_needs_a_declared_year():
    assert "not declared" in M.window(_const(fy=2030), _cal())[1]


def test_window_unknown_basis_is_refused():
    assert "Rate Basis" in M.window({"rate_basis": "Other", "fiscal_year": 2025}, _cal())[1]


# -- T2: row rules -----------------------------------------------------------------

def test_rows_valid_statutory_and_constant_have_no_problems():
    assert M.row_problems(_stat()) == []
    assert M.row_problems(_const()) == []


def test_rows_pair_is_unique_per_sheet():
    probs = M.row_problems(_stat(rows=[_srow(), _srow(avg=1, cls=1)]))
    assert len(probs) == 1 and "EUR → CHF" in probs[0] and "more than once" in probs[0]
    assert M.row_problems(_stat(rows=[_srow(), _srow(f="USD")])) == []


def test_rows_from_must_differ_from_to():
    probs = M.row_problems(_stat(rows=[_srow(f="CHF", t="CHF")]))
    assert any("same currency" in p for p in probs)


def test_rows_statutory_needs_both_quotes():
    probs = M.row_problems(_stat(rows=[_srow(avg=None), _srow(f="USD", cls=0)]))
    assert any("EUR → CHF" in p and "Average Quote" in p for p in probs)
    assert any("USD → CHF" in p and "Closing Quote" in p for p in probs)


def test_rows_constant_needs_a_rate():
    probs = M.row_problems(_const(rows=[_crow(rate=None), _crow(f="USD", rate=-1)]))
    assert any("EUR → CHF" in p and "Rate" in p for p in probs)
    assert any("USD → CHF" in p and "Rate" in p for p in probs)


def test_rows_constant_does_not_need_statutory_quotes_and_vice_versa():
    assert M.row_problems(_const(rows=[_crow(average_quote=None, closing_quote=None)])) == []
    assert M.row_problems(_stat(rows=[_srow(rate=None)])) == []


def test_rows_quoted_per_is_one_of_the_five():
    for ok in (1, 10, 100, 1000, 10000, "100"):
        assert M.row_problems(_stat(rows=[_srow(per=ok)])) == []
    for bad in (0, 2, 5, 100000, None, "x", 1.5):
        probs = M.row_problems(_stat(rows=[_srow(per=bad)]))
        assert any("Quoted Per" in p for p in probs), bad


def test_rows_a_sheet_needs_at_least_one_row():
    assert any("no rates" in p for p in M.row_problems(_stat(rows=[])))


# -- T3: published rows ------------------------------------------------------------

def test_published_statutory_gives_average_and_closing_per_pair():
    rows = M.published_rows(_stat(rows=[_srow(avg=0.95, cls=0.96)]), _cal(), "ACTUAL")
    assert rows == [["CHF", "EUR", 2025, 6, "Average", 0.95, "RS-S-2025-06", "ACTUAL"],
                    ["CHF", "EUR", 2025, 6, "Closing", 0.96, "RS-S-2025-06", "ACTUAL"]]


def test_published_statutory_rate_is_quote_over_per_in_decimal():
    """0.6607 per 100 is 0.006607; float division gives 0.006606999999999999."""
    rows = M.published_rows(_stat(rows=[_srow("JPY", "USD", avg=0.6607, cls=0.6607, per=100)]), _cal(), "ACTUAL")
    assert rows[0][5] == 0.006607 and rows[1][5] == 0.006607
    assert 0.6607 / 100 != 0.006607  # the float division this guards against


def test_published_matches_group_rates_true_rate():
    src = open(os.path.join(APP_DIR, "group_rates.py")).read()
    assert "float(decimal.Decimal(str(quote or 0)) / int(quoted_per or 1))" in src
    assert M.true_rate(0.6607, 100) == 0.006607 and M.true_rate(1.0852, "1") == 1.0852


def test_published_statutory_uses_the_actuals_code_given():
    rows = M.published_rows(_stat(), _cal(), "BASE")
    assert {r[7] for r in rows} == {"BASE"}


def test_published_two_pairs_four_rows_in_a_stable_order():
    sheet = _stat(rows=[_srow("USD", "CHF", 0.9, 0.91), _srow("EUR", "CHF", 0.95, 0.96)])
    rows = M.published_rows(sheet, _cal(), "ACTUAL")
    assert [(r[1], r[4]) for r in rows] == [("USD", "Average"), ("USD", "Closing"),
                                            ("EUR", "Average"), ("EUR", "Closing")]


def test_published_constant_gives_both_types_at_the_one_rate_for_each_period():
    rows = M.published_rows(_const(rows=[_crow(rate=94, per=100)]), _cal(), "ACTUAL")
    assert {r[7] for r in rows} == {"CONSTANT"}
    assert {r[6] for r in rows} == {"RS-C-2025"}
    assert {r[5] for r in rows} == {0.94}
    periods = sorted({r[3] for r in rows})
    assert periods == [0] + list(range(1, 13))  # Opening and Regular, one row pair each
    for p in periods:
        assert sorted(r[4] for r in rows if r[3] == p) == ["Average", "Closing"]
    assert len(rows) == 2 * 13


def test_published_constant_closing_period_is_left_to_the_warehouse_map():
    """rate_period_map() reads a Closing period at the year's last Regular
    period, so a P99 row would only be a second entry of P12's."""
    rows = M.published_rows(_const(), _cal(), "ACTUAL")
    assert 99 not in {r[3] for r in rows}


def test_published_constant_closing_period_is_published_when_the_year_has_no_regular_period():
    """rate_period_map() maps a Closing period in a year with no Regular period
    to itself, so it needs a rate of its own."""
    cal = [r for r in _cal() if r["type"] == "Closing"]
    rows = M.published_rows(_const(), cal, "ACTUAL")
    assert {r[3] for r in rows} == {99}


def test_published_constant_adjustment_periods_are_published():
    """An Adjustment period maps to itself in rate_period_map()."""
    rows = M.published_rows(_const(), _cal(adjustments=(13,)), "ACTUAL")
    assert 13 in {r[3] for r in rows}


def test_published_constant_code_is_a_parameter():
    rows = M.published_rows(_const(), _cal(), "ACTUAL", constant_code="K")
    assert {r[7] for r in rows} == {"K"}


def test_published_constant_follows_the_calendar_of_its_year_only():
    rows = M.published_rows(_const(), _cal(2024, regular=4) + _cal(2025, regular=2), "ACTUAL")
    assert sorted({r[3] for r in rows}) == [0, 1, 2] and {r[2] for r in rows} == {2025}


def test_published_constant_refuses_an_undeclared_year():
    try:
        M.published_rows(_const(fy=2030), _cal(), "ACTUAL")
    except ValueError as e:
        assert "not declared" in str(e)
    else:
        raise AssertionError("an undeclared year must raise")


def test_published_statutory_refuses_a_period_the_window_refuses():
    try:
        M.published_rows(_stat(fp=99), _cal(), "ACTUAL")
    except ValueError as e:
        assert "Closing" in str(e)
    else:
        raise AssertionError("a Closing period must raise")


def test_published_rows_of_an_invalid_sheet_raise():
    try:
        M.published_rows(_stat(rows=[_srow(per=7)]), _cal(), "ACTUAL")
    except ValueError as e:
        assert "Quoted Per" in str(e)
    else:
        raise AssertionError("an invalid row must raise")


# -- T4: the move check ------------------------------------------------------------

def test_move_threshold_message_is_the_close_policy_message():
    assert M.RATE_MOVE_MESSAGE == POLICY.RATE_MOVE_MESSAGE


def test_move_inside_the_threshold_is_clean():
    prev = _stat(fp=5, rows=[_srow(avg=0.95, cls=0.96)])
    new = _stat(fp=6, rows=[_srow(avg=0.96, cls=0.97)])
    assert M.move_problems(new, prev, 0.5) == []


def test_move_over_the_threshold_names_the_pair_type_and_change():
    prev = _stat(fp=5, rows=[_srow(avg=1.0, cls=1.0)])
    new = _stat(fp=6, rows=[_srow(avg=1.6, cls=1.0)])
    probs = M.move_problems(new, prev, 0.5)
    assert len(probs) == 1
    assert "EUR → CHF Average" in probs[0] and "+60%" in probs[0] and "RS-S-2025-05" in probs[0]
    assert "Reason for Change" in probs[0]


def test_move_exactly_at_the_threshold_is_clean():
    prev = _stat(fp=5, rows=[_srow(avg=1.0, cls=1.0)])
    assert M.move_problems(_stat(rows=[_srow(avg=1.5, cls=0.5)]), prev, 0.5) == []


def test_move_compares_true_rates_not_quotes():
    """100 JPY quoted per 100 vs per 1: same rate, no move."""
    prev = _stat(fp=5, rows=[_srow("JPY", "USD", avg=0.66, cls=0.66, per=100)])
    new = _stat(fp=6, rows=[_srow("JPY", "USD", avg=0.0066, cls=0.0066, per=1)])
    assert M.move_problems(new, prev, 0.1) == []


def test_move_a_pair_with_no_previous_rate_needs_no_reason():
    prev = _stat(fp=5, rows=[_srow("USD", "CHF")])
    assert M.move_problems(_stat(rows=[_srow("EUR", "CHF", avg=99, cls=99)]), prev, 0.5) == []
    assert M.move_problems(_stat(), None, 0.5) == []


def test_move_undeclared_threshold_with_a_reference_names_close_settings_once():
    prev = _stat(fp=5, rows=[_srow(avg=1.0, cls=1.0), _srow("USD", avg=1.0, cls=1.0)])
    new = _stat(fp=6, rows=[_srow(avg=1.1, cls=1.1), _srow("USD", avg=1.1, cls=1.1)])
    assert M.move_problems(new, prev, None) == [M.RATE_MOVE_MESSAGE]


def test_move_undeclared_threshold_with_nothing_to_compare_is_clean():
    assert M.move_problems(_stat(), None, None) == []
    assert M.move_problems(_stat(), _stat(fp=5, rows=[_srow("USD")]), None) == []


def test_move_also_checks_the_erp_quote_it_was_proposed_from():
    new = _stat(rows=[_srow(avg=1.0, cls=1.0, erp_average_quote=3.0, erp_closing_quote=1.0)])
    probs = M.move_problems(new, None, 0.5)
    assert len(probs) == 1 and "Average" in probs[0] and "the ERP quote" in probs[0]


def test_move_constant_sheets_compare_the_rate():
    prev = _const(fy=2024, rows=[_crow(rate=1.0)])
    probs = M.move_problems(_const(rows=[_crow(rate=2.0)]), prev, 0.5)
    assert len(probs) == 1 and "EUR → CHF" in probs[0] and "+100%" in probs[0]
    assert M.move_problems(_const(rows=[_crow(rate=1.2)]), prev, 0.5) == []


def test_move_refuses_to_compare_two_different_bases():
    try:
        M.move_problems(_const(), _stat(), 0.5)
    except ValueError as e:
        assert "Rate Basis" in str(e)
    else:
        raise AssertionError("mixed bases must raise")


# -- T4: duplicate ------------------------------------------------------------------

def test_duplicate_copies_every_row_as_a_duplicated_draft():
    prev = _stat(fp=5, rows=[_srow(avg=0.95, cls=0.96, erp_average_quote=0.9, erp_closing_quote=0.9,
                                   source="ERP pre-fill", quote_label="x", legacy_documents="GER-1"),
                             _srow("JPY", "USD", avg=0.66, cls=0.67, per=100)])
    plan, why = M.plan_duplicate(prev, ("Statutory", 2025, 6), set())
    assert why is None
    assert (plan["rate_basis"], plan["fiscal_year"], plan["fiscal_period"]) == ("Statutory", 2025, 6)
    assert plan["copied_from"] == "RS-S-2025-05"
    assert "RS-S-2025-05" in plan["source_note"] and "FY2025 P5" in plan["source_note"]
    assert plan["rows"][0] == {"from_currency": "EUR", "to_currency": "CHF", "quoted_per": 1,
                               "average_quote": 0.95, "closing_quote": 0.96, "source": "Duplicated"}
    assert plan["rows"][1]["quoted_per"] == 100 and plan["rows"][1]["average_quote"] == 0.66
    assert plan["rows"] is not prev["rows"]


def test_duplicate_into_the_next_year_for_a_constant_sheet():
    plan, why = M.plan_duplicate(_const(fy=2025, rows=[_crow(rate=0.94)]), ("Constant", 2026, None), set())
    assert why is None
    assert plan["fiscal_year"] == 2026 and plan["fiscal_period"] is None
    assert plan["rows"] == [{"from_currency": "EUR", "to_currency": "CHF", "quoted_per": 1,
                             "rate": 0.94, "source": "Duplicated"}]
    assert "FY2025" in plan["source_note"] and "RS-C-2025" in plan["source_note"]


def test_duplicate_only_an_approved_sheet_is_a_source():
    for ds in (0, 2):
        plan, why = M.plan_duplicate(_stat(fp=5, docstatus=ds), ("Statutory", 2025, 6), set())
        assert plan is None and "approved" in why


def test_duplicate_a_key_that_already_has_a_sheet_is_refused_by_name():
    plan, why = M.plan_duplicate(_stat(fp=5), ("Statutory", 2025, 6), {("Statutory", 2025, 6)})
    assert plan is None and "FY2025 P6" in why and "supersede" in why.lower()
    plan, why = M.plan_duplicate(_const(), ("Constant", 2026, None), {("Constant", 2026, None)})
    assert plan is None and "FY2026" in why


def test_duplicate_other_keys_do_not_block():
    plan, why = M.plan_duplicate(_stat(fp=5), ("Statutory", 2025, 6), {("Statutory", 2025, 7), ("Constant", 2025, None)})
    assert why is None and plan


def test_duplicate_target_must_match_the_source_basis():
    plan, why = M.plan_duplicate(_stat(fp=5), ("Constant", 2025, None), set())
    assert plan is None and "Rate Basis" in why


def test_duplicate_a_statutory_target_needs_a_period_and_a_constant_one_none():
    assert "Fiscal Period" in M.plan_duplicate(_stat(fp=5), ("Statutory", 2025, None), set())[1]
    assert "no Fiscal Period" in M.plan_duplicate(_const(), ("Constant", 2026, 3), set())[1]


def test_duplicate_into_the_source_itself_is_refused():
    plan, why = M.plan_duplicate(_stat(fp=5), ("Statutory", 2025, 5), set())
    assert plan is None and "itself" in why


def test_duplicate_a_malformed_target_key_is_refused():
    assert M.plan_duplicate(_stat(fp=5), ("Statutory", 2025), set())[0] is None

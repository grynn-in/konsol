"""Persona and landing period, pure: konsol/close/period_model.py (konsol#305 A03, D5 of #298).

Loaded by path; the module imports nothing from frappe or konsol.
"""
import ast
import datetime
import importlib.util
import os

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODEL_PATH = os.path.join(APP_DIR, "close", "period_model.py")
_spec = importlib.util.spec_from_file_location("close_period_model_under_test", MODEL_PATH)
M = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(M)

D = datetime.date
TODAY = D(2026, 10, 15)


def _row(fy, fp, start, end, status="Open", period_type="Regular"):
    return {
        "fiscal_year": fy,
        "fiscal_period": fp,
        "period_code": "FY%d-P%02d" % (fy, fp),
        "period_label": "P%02d" % fp,
        "period_type": period_type,
        "start_date": start,
        "end_date": end,
        "quarter": None,
        "status": status,
    }


def _fy2026(statuses=None):
    """FY2026 Jan-Dec; P01-P08 Closed, P09-P12 Open unless overridden."""
    rows = []
    for m in range(1, 13):
        start = D(2026, m, 1)
        end = (D(2026, m + 1, 1) if m < 12 else D(2027, 1, 1)) - datetime.timedelta(days=1)
        status = "Closed" if m <= 8 else "Open"
        if statuses and m in statuses:
            status = statuses[m]
        rows.append(_row(2026, m, start, end, status))
    return rows


# --- the non-viewer landing -------------------------------------------------

EARLY = (2000, 1)


def test_lands_on_the_oldest_open_ended_period_not_the_calendar_month():
    res = M.landing(_fy2026(), set(), "close_lead", TODAY, first_close=EARLY)
    assert res["period"] == (2026, 9)
    assert res["rule"] == "oldest_open_ended"


def test_the_oldest_open_ended_period_wins_across_years():
    rows = [
        _row(2025, 12, D(2025, 12, 1), D(2025, 12, 31)),
        _row(2026, 1, D(2026, 1, 1), D(2026, 1, 31)),
        _row(2026, 2, D(2026, 2, 1), D(2026, 2, 28)),
    ]
    res = M.landing(rows, set(), "group_accountant", D(2026, 2, 10), first_close=EARLY)
    assert res["period"] == (2025, 12)
    assert res["rule"] == "oldest_open_ended"


def test_no_open_ended_period_lands_on_the_period_containing_today():
    rows = _fy2026(statuses={9: "Closed"})
    res = M.landing(rows, set(), "entity_accountant", TODAY, first_close=EARLY)
    assert res["period"] == (2026, 10)
    assert res["rule"] == "contains_today"


def test_a_period_ending_today_is_not_ended():
    rows = _fy2026(statuses={9: "Closed"})
    res = M.landing(rows, set(), "close_lead", D(2026, 10, 31), first_close=EARLY)
    assert res["period"] == (2026, 10)
    assert res["rule"] == "contains_today"


def test_open_ended_adjustment_and_closing_rows_are_ignored():
    rows = _fy2026(statuses={9: "Closed"})
    rows.append(_row(2026, 13, D(2026, 1, 1), D(2026, 1, 31), period_type="Adjustment"))
    rows.append(_row(2026, 14, D(2026, 1, 1), D(2026, 1, 31), period_type="Closing"))
    res = M.landing(rows, set(), "close_lead", TODAY, first_close=EARLY)
    assert res["period"] == (2026, 10)
    assert res["rule"] == "contains_today"


def test_an_adjustment_row_containing_today_is_not_landed_on():
    rows = [_row(2026, 13, D(2026, 10, 1), D(2026, 10, 31), status="Closed", period_type="Adjustment")]
    res = M.landing(rows, set(), "close_lead", TODAY, first_close=EARLY)
    assert res["period"] is None


def test_string_dates_are_accepted():
    rows = [_row(2026, 9, "2026-09-01", "2026-09-30")]
    res = M.landing(rows, set(), "close_lead", TODAY, first_close=EARLY)
    assert res["period"] == (2026, 9)


def test_nothing_open_and_nothing_containing_today_is_a_named_gap_not_a_guess():
    rows = [_row(2025, m, D(2025, m, 1), D(2025, m, 28), status="Closed") for m in range(1, 13)]
    res = M.landing(rows, set(), "close_lead", TODAY, first_close=EARLY)
    assert res["period"] is None
    assert res["rule"] is None
    assert "2026-10-15" in res["reason"]
    assert "EPM Fiscal Year" in res["reason"]


def test_no_rows_at_all_is_the_same_named_gap():
    res = M.landing([], set(), "close_lead", TODAY, first_close=EARLY)
    assert res["period"] is None
    assert "EPM Fiscal Year" in res["reason"]


def test_a_found_landing_has_no_reason():
    res = M.landing(_fy2026(), set(), "close_lead", TODAY, first_close=EARLY)
    assert res["reason"] is None


# --- the viewer ---------------------------------------------------------------

def test_viewer_lands_on_the_latest_signed_period_with_the_provisional_switch():
    rows = _fy2026()
    signed = {(2026, 7), (2026, 8), (2025, 12)}
    res = M.landing(rows, signed, "viewer", TODAY, first_close=EARLY)
    assert res["period"] == (2026, 8)
    assert res["rule"] == "latest_signed"
    assert res["reason"] is None
    assert res["provisional"] == M.landing(rows, signed, "close_lead", TODAY, first_close=EARLY)
    assert res["provisional"]["period"] == (2026, 9)


def test_viewer_with_nothing_signed_gets_no_period_and_a_reason():
    rows = _fy2026()
    res = M.landing(rows, set(), "viewer", TODAY, first_close=EARLY)
    assert res["period"] is None
    assert res["rule"] is None
    assert "No period has been signed off yet" in res["reason"]
    assert res["provisional"] == M.landing(rows, set(), "close_lead", TODAY, first_close=EARLY)
    assert res["provisional"]["period"] == (2026, 9)


def test_non_viewers_ignore_signed_keys():
    res = M.landing(_fy2026(), {(2026, 8)}, "close_lead", TODAY, first_close=EARLY)
    assert res["period"] == (2026, 9)
    assert "provisional" not in res or res["provisional"] is None


# --- persona ------------------------------------------------------------------

PERSONA_CASES = [
    (["EPM Admin"], "close_lead"),
    (["System Manager"], "close_lead"),
    (["EPM User", "EPM Analyst", "EPM Admin"], "close_lead"),
    (["EPM User", "Entity Accountant", "EPM Analyst"], "group_accountant"),
    (["EPM User", "Entity Accountant"], "entity_accountant"),
    (["EPM User"], "viewer"),
    (["Guest", "Budget Submitter", "Budget Approver"], None),
    ([], None),
]


def test_persona_priority():
    for roles, expected in PERSONA_CASES:
        assert M.persona(roles) == expected, roles


def _raises_value_error(fn):
    try:
        fn()
    except ValueError:
        return True
    return False


def test_landing_without_a_persona_raises():
    assert _raises_value_error(lambda: M.landing(_fy2026(), set(), None, TODAY, first_close=EARLY))


def test_landing_with_an_unknown_persona_raises():
    assert _raises_value_error(lambda: M.landing(_fy2026(), set(), "system", TODAY, first_close=EARLY))


def test_module_imports_no_frappe():
    with open(MODEL_PATH) as fh:
        tree = ast.parse(fh.read())
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            assert not any(a.name.startswith(("frappe", "konsol")) for a in node.names)
        if isinstance(node, ast.ImportFrom):
            assert not (node.module or "").startswith(("frappe", "konsol"))
            assert node.level == 0



# --- A03b: landing honours the declared first close period (konsol#303) ----
# Measured live 25 Sep 2026: every period since 2010 is Open, so the plain D5
# rule lands everyone on 2010 P1. Only periods from the first close period on
# are candidates; with none declared, the landing is a named gap, not a guess.

def _years(*years):
    rows = []
    for y in years:
        for p in range(1, 13):
            end_day = 28 if p == 2 else 30
            rows.append({"fiscal_year": y, "fiscal_period": p, "period_type": "Regular", "status": "Open",
                         "start_date": D(y, p, 1), "end_date": D(y, p, end_day)})
    return rows


def test_history_before_the_first_close_period_is_never_landed_on():
    res = M.landing(_years(2010, 2025, 2026), set(), "close_lead", D(2026, 10, 3), first_close=(2025, 7))
    assert res["period"] == (2025, 7), res
    assert res["rule"] == "oldest_open_ended"


def test_an_undeclared_first_close_period_is_a_named_gap():
    res = M.landing(_years(2010, 2026), set(), "group_accountant", D(2026, 10, 3), first_close=None)
    assert res["period"] is None
    assert res["rule"] == "first_close_undeclared"
    assert "first close period" in res["reason"]


def test_the_viewer_still_lands_on_the_latest_signed_without_a_first_close_period():
    res = M.landing(_years(2026), {(2026, 8)}, "viewer", D(2026, 10, 3), first_close=None)
    assert res["period"] == (2026, 8)
    assert res["provisional"]["rule"] == "first_close_undeclared"


def test_first_close_is_required():
    try:
        M.landing(_years(2026), set(), "close_lead", D(2026, 10, 3))
    except TypeError:
        return
    raise AssertionError("landing() must require first_close: no default decides where closing starts")

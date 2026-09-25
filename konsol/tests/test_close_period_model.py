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


def test_first_close_undeclared_reason_names_close_settings_not_epm_settings():
    # konsol#305 A36b: the first close period lives in Close Settings, a
    # doctype the Close Lead (EPM Admin) can write; EPM Settings is
    # System Manager only (A36 was proved impossible live).
    res = M.landing(_years(2026), set(), "close_lead", D(2026, 10, 3), first_close=None)
    assert "Close Settings" in res["reason"]
    assert "EPM Settings" not in res["reason"]


def test_first_close_is_required():
    try:
        M.landing(_years(2026), set(), "close_lead", D(2026, 10, 3))
    except TypeError:
        return
    raise AssertionError("landing() must require first_close: no default decides where closing starts")


# --- A04: period states, other open periods, the catch-up label -------------
# Measured live 25 Sep 2026: submitted TBs exist for 2024 P12 and 2025 P07-P12;
# 2025 P01-P06 were never loaded. With the first close period at 2025 P07, its
# catch-up TB covers 2025 P01 onwards: the period after the previous loaded one.

def _state(result, key):
    matches = [s for s in result["states"] if s["key"] == key]
    assert len(matches) == 1, (key, matches)
    return matches[0]


def _live_rows():
    return _years(2024, 2025, 2026)


LIVE_LOADED = {(2024, 12), (2025, 7), (2025, 8), (2025, 9), (2025, 10), (2025, 11), (2025, 12)}


def test_each_regular_row_gets_a_state_with_every_field():
    rows = _fy2026()
    rows.append(_row(2026, 13, D(2026, 12, 31), D(2026, 12, 31), period_type="Adjustment"))
    res = M.period_states(rows, {}, (2026, 1), set())
    assert [s["key"] for s in res["states"]] == [(2026, m) for m in range(1, 13)]
    for s in res["states"]:
        assert set(s) >= {"key", "code", "label", "status", "checks", "signoff",
                          "is_signed", "is_history", "catch_up"}, s
    p9 = _state(res, (2026, 9))
    assert p9["code"] == "FY2026-P09"
    assert p9["label"] == "P09"
    assert p9["status"] == "Open"
    assert _state(res, (2026, 1))["status"] == "Closed"


def test_states_are_ordered_oldest_first_whatever_the_input_order():
    rows = list(reversed(_fy2026()))
    res = M.period_states(rows, {}, (2026, 1), set())
    assert [s["key"] for s in res["states"]] == [(2026, m) for m in range(1, 13)]


def test_a_period_with_no_run_is_not_run_and_not_signed_off():
    res = M.period_states(_fy2026(), {}, (2026, 1), set())
    p9 = _state(res, (2026, 9))
    assert p9["checks"] == "Not run"
    assert p9["signoff"] == "Not signed off"
    assert p9["is_signed"] is False
    assert p9["run"] is None


def test_a_run_supplies_checks_and_signoff():
    runs = {(2026, 8): {"name": "AR-1", "status": "Amber", "signoff_status": "Acknowledged"},
            (2026, 7): {"name": "AR-2", "status": "Green", "signoff_status": "Not Signed Off"}}
    res = M.period_states(_fy2026(), runs, (2026, 1), set())
    p8, p7 = _state(res, (2026, 8)), _state(res, (2026, 7))
    assert (p8["checks"], p8["signoff"], p8["is_signed"], p8["run"]) == ("Amber", "Acknowledged", True, "AR-1")
    assert (p7["checks"], p7["signoff"], p7["is_signed"]) == ("Green", "Not Signed Off", False)


def test_every_signed_state_counts_as_signed():
    for signoff in ("Signed Off", "Acknowledged", "Overridden"):
        runs = {(2026, 8): {"name": "AR-1", "status": "Green", "signoff_status": signoff}}
        assert _state(M.period_states(_fy2026(), runs, (2026, 1), set()), (2026, 8))["is_signed"] is True, signoff


def test_re_sign_needed_passes_through_and_is_not_signed():
    runs = {(2026, 8): {"name": "AR-1", "status": "Green", "signoff_status": "Re-sign Needed"}}
    p8 = _state(M.period_states(_fy2026(), runs, (2026, 1), set()), (2026, 8))
    assert p8["signoff"] == "Re-sign Needed"
    assert p8["is_signed"] is False


def test_a_run_with_a_blank_signoff_is_not_signed_off():
    runs = {(2026, 8): {"name": "AR-1", "status": "Error", "signoff_status": None}}
    p8 = _state(M.period_states(_fy2026(), runs, (2026, 1), set()), (2026, 8))
    assert (p8["checks"], p8["signoff"], p8["is_signed"]) == ("Error", "Not signed off", False)


def test_periods_before_the_first_close_period_are_history():
    res = M.period_states(_live_rows(), {}, (2025, 7), LIVE_LOADED)
    assert _state(res, (2025, 6))["is_history"] is True
    assert _state(res, (2024, 12))["is_history"] is True
    assert _state(res, (2025, 7))["is_history"] is False
    assert _state(res, (2026, 1))["is_history"] is False
    assert res["config_gaps"] == []


def test_an_undeclared_first_close_period_assumes_nothing_and_is_a_named_gap():
    res = M.period_states(_live_rows(), {}, None, LIVE_LOADED)
    assert all(s["is_history"] is None for s in res["states"])
    assert all(s["catch_up"] is None for s in res["states"])
    assert [g["code"] for g in res["config_gaps"]] == ["first_close_undeclared"]
    assert "Close Settings" in res["config_gaps"][0]["message"]


def test_first_close_is_required():
    try:
        M.period_states(_live_rows(), {}, loaded_keys=set())
    except TypeError:
        return
    raise AssertionError("period_states() must require first_close: no default decides history")


def test_live_shape_catch_up_covers_from_the_period_after_the_previous_loaded_one():
    res = M.period_states(_live_rows(), {}, (2025, 7), LIVE_LOADED)
    assert _state(res, (2025, 7))["catch_up"] == "Catch-up: covers from FY2025 P01"
    others = [s for s in res["states"] if s["key"] != (2025, 7)]
    assert all(s["catch_up"] is None for s in others)


def test_catch_up_starts_mid_year_after_a_mid_year_load():
    loaded = {(2025, 3), (2025, 7)}
    res = M.period_states(_live_rows(), {}, (2025, 7), loaded)
    assert _state(res, (2025, 7))["catch_up"] == "Catch-up: covers from FY2025 P04"


def test_no_catch_up_when_every_earlier_period_of_its_year_was_loaded():
    loaded = {(2025, p) for p in range(1, 8)}
    res = M.period_states(_live_rows(), {}, (2025, 7), loaded)
    assert _state(res, (2025, 7))["catch_up"] is None


def test_no_catch_up_when_the_first_close_period_opens_its_year():
    # 2024 P12 loaded, first close 2025 P01: no earlier Regular period of its year exists.
    res = M.period_states(_live_rows(), {}, (2025, 1), {(2024, 12)})
    assert _state(res, (2025, 1))["catch_up"] is None
    # ... even when earlier years were never loaded at all.
    res = M.period_states(_live_rows(), {}, (2025, 1), set())
    assert _state(res, (2025, 1))["catch_up"] is None


def test_catch_up_with_nothing_loaded_before_covers_from_the_start_of_its_year():
    res = M.period_states(_live_rows(), {}, (2025, 7), {(2025, 7)})
    assert _state(res, (2025, 7))["catch_up"] == "Catch-up: covers from FY2025 P01"


def test_catch_up_ignores_non_regular_rows():
    rows = _live_rows()
    rows.append(_row(2025, 0, D(2025, 1, 1), D(2025, 1, 1), period_type="Opening"))
    res = M.period_states(rows, {}, (2025, 7), LIVE_LOADED | {(2025, 0)})
    assert _state(res, (2025, 7))["catch_up"] == "Catch-up: covers from FY2025 P01"
    assert all(s["key"] != (2025, 0) for s in res["states"])


def test_other_open_lists_open_non_history_periods_oldest_first():
    rows = _live_rows()
    for r in rows:
        if (r["fiscal_year"], r["fiscal_period"]) in {(2025, 8), (2026, 2)}:
            r["status"] = "Closed"
    states = M.period_states(rows, {}, (2025, 7), LIVE_LOADED)["states"]
    keys = [s["key"] for s in M.other_open(states, (2025, 9), (2025, 7))]
    expected = [(2025, p) for p in (7, 10, 11, 12)] + [(2026, p) for p in range(1, 13) if p != 2]
    assert keys == expected
    assert (2025, 6) not in keys and (2010, 1) not in keys


def test_other_open_never_reports_history_even_if_states_are_unflagged():
    # States built without a first close period carry is_history None; other_open
    # still applies the first close period it is given.
    states = M.period_states(_live_rows(), {}, None, set())["states"]
    keys = [s["key"] for s in M.other_open(states, (2026, 1), (2025, 7))]
    assert keys[0] == (2025, 7)
    assert all(k >= (2025, 7) for k in keys)


def test_other_open_with_no_first_close_period_reports_nothing():
    states = M.period_states(_live_rows(), {}, None, set())["states"]
    assert M.other_open(states, (2026, 1), None) == []


def test_other_open_excludes_the_selected_period():
    states = M.period_states(_live_rows(), {}, (2025, 7), set())["states"]
    keys = [s["key"] for s in M.other_open(states, (2025, 7), (2025, 7))]
    assert (2025, 7) not in keys and keys[0] == (2025, 8)


def test_signed_states_match_the_assertion_run_controller():
    # One source of truth: the pure model mirrors assertion_run.SIGNED_STATES.
    path = os.path.join(APP_DIR, "consolidation", "doctype", "assertion_run", "assertion_run.py")
    with open(path, encoding="utf-8") as fh:
        tree = ast.parse(fh.read())
    found = [ast.literal_eval(n.value) for n in tree.body if isinstance(n, ast.Assign)
             and any(getattr(t, "id", None) == "SIGNED_STATES" for t in n.targets)]
    assert found == [M.SIGNED_STATES]


def test_re_sign_needed_is_not_a_signed_state():
    assert "Re-sign Needed" not in M.SIGNED_STATES

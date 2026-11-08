"""Close home dashboard, pure layout (trial, Deepak Pai, 8 Nov 2026).

``home_model`` lays out the one "Konsol Home Dashboard" block from what the
close app's readers already return (charts_model charts, ic_api.get_ic,
period states, get_my_work counts, setup.status, freshness). It decides no
close rule. What this pins:

- a tile's value is its newest point; its delta is against the point before;
  a point with no value is a gap with its reason, never 0;
- a tile whose newest point has no value is an empty state naming why and
  linking to the action, never a zero;
- sparklines break at a gap and never invent a point;
- every route is a /desk/ path.
"""
import importlib.util
import os

import pytest

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, os.path.join(APP_DIR, rel))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


M = _load("test_home_model_home_model", os.path.join("close", "home_model.py"))
CM = _load("test_home_model_charts_model", os.path.join("close", "charts_model.py"))

P05, P06, P07 = (2025, 5), (2025, 6), (2025, 7)


def _state(key, status="Open", end="2025-07-31", checks="Not run", signoff="Not Signed Off",
           signed=False):
    return {"key": key, "status": status, "end_date": end, "checks": checks,
            "signoff": signoff, "is_signed": signed}


# --- window ------------------------------------------------------------------

def test_window_is_the_last_n_periods_up_to_the_current_one_oldest_first():
    states = [_state((2024, 11)), _state((2024, 12)), _state((2025, 1)), _state((2025, 2)),
              _state((2025, 3))]
    assert M.window(states, (2025, 2), 3) == [(2024, 12), (2025, 1), (2025, 2)]
    assert M.window(states, (2025, 2), 6) == [(2024, 11), (2024, 12), (2025, 1), (2025, 2)]


def test_window_refuses_a_count_outside_the_choices():
    with pytest.raises(ValueError):
        M.window([_state(P07)], P07, 5)


# --- sparkline ----------------------------------------------------------------

def test_spark_breaks_at_a_gap_and_never_invents_a_point():
    runs = M.spark([1, 2, None, 3], width=60, height=20)
    assert len(runs) == 2
    assert len(runs[0].split()) == 2 and len(runs[1].split()) == 1


def test_spark_of_no_values_is_empty():
    assert M.spark([None, None]) == []
    assert M.spark([]) == []


def test_spark_scales_min_to_bottom_and_max_to_top():
    (run,) = M.spark([0, 10], width=60, height=20)
    (x0, y0), (x1, y1) = [tuple(float(v) for v in p.split(",")) for p in run.split()]
    assert x0 == 0 and x1 == 60
    assert y0 > y1  # SVG y grows down: the larger value is higher


def test_spark_of_a_constant_series_is_a_level_line():
    (run,) = M.spark([4, 4, 4])
    ys = {p.split(",")[1] for p in run.split()}
    assert len(ys) == 1


# --- tile ---------------------------------------------------------------------

def _points(*values):
    labels = ["FY2025 P%02d" % (i + 1) for i in range(len(values))]
    return [{"label": l, "value": v, "display": None if v is None else str(v), "note": None}
            for l, v in zip(labels, values)]


def test_tile_value_is_the_newest_point_and_delta_is_against_the_prior():
    t = M.tile("IC differences", _points(3, 5), route="/desk/ic-balance", higher_is_better=False)
    assert t["state"] == M.OK
    assert t["value"] == 5 and t["prior"] == 3 and t["delta"] == 2
    assert t["delta_display"] == "+2 vs FY2025 P01"
    assert t["delta_tone"] == "bad"  # more differences is worse
    assert t["route"] == "/desk/ic-balance"
    assert t["spark"]


def test_tile_delta_is_none_when_the_prior_point_is_a_gap():
    t = M.tile("Days to close", _points(None, 4), route="/desk/assertion-run",
               higher_is_better=False)
    assert t["value"] == 4 and t["prior"] is None and t["delta"] is None
    assert t["delta_display"] is None


def test_tile_no_change_is_said():
    t = M.tile("TB submitted", _points(7, 7), route="/desk/trial-balance-submission",
               higher_is_better=True)
    assert t["delta"] == 0 and t["delta_display"] == "No change vs FY2025 P01"
    assert t["delta_tone"] == "flat"


def test_tile_with_a_gap_newest_point_is_an_empty_state_with_its_action():
    pts = _points(2, None)
    pts[-1]["note"] = "No trial balance for FY2025 P02 yet"
    t = M.tile("TB submitted", pts, route="/desk/trial-balance-submission",
               higher_is_better=True,
               empty_action=("Upload", "/desk/trial-balance-upload"))
    assert t["state"] == M.EMPTY
    assert t["value"] is None and t["display"] is None
    assert t["empty"] == {"text": "No trial balance for FY2025 P02 yet",
                          "action": "Upload", "route": "/desk/trial-balance-upload"}


def test_tile_refuses_a_route_outside_the_desk():
    with pytest.raises(ValueError):
        M.tile("X", _points(1), route="https://example.com", higher_is_better=True)


def test_unavailable_names_its_reason():
    assert M.unavailable("the warehouse is not built") == {
        "state": M.UNAVAILABLE, "reason": "the warehouse is not built"}


# --- tile points from the existing readers -------------------------------------

def test_tb_points_count_submitted_and_declared_exceptions_as_in():
    chart = CM.tb_progress(
        [P06, P07],
        {P06: {"expected": ["A", "B"], "missing": []}, P07: {"expected": ["A", "B", "C"],
                                                          "missing": ["C"]}},
        {P06: {"A", "B"}, P07: {"A"}}, {P07: {"B"}}, None)
    pts = M.tb_points(chart)
    assert [p["value"] for p in pts] == [2, 2]
    assert pts[-1]["display"] == "2 / 3"
    assert M.tb_breakdown(chart) == "1 submitted · 1 exception · 1 missing"


def test_tb_points_with_nobody_expected_is_a_gap_with_its_reason():
    chart = CM.tb_progress([P07], {P07: {"expected": [], "missing": []}}, {}, {}, None)
    (p,) = M.tb_points(chart)
    assert p["value"] is None
    assert "No entity owes a trial balance" in p["note"]


def _ic(over=0, within=1, state="checked", group="G1", unmatched=0):
    pairs = ([{"match_status": "over_tolerance", "entity_a": "A", "entity_b": "B"}] * over
             + [{"match_status": "matched", "entity_a": "A", "entity_b": "C"}] * within)
    return {"state": state, "message": "IC message", "groups": [
        {"consolidation_group": group, "pairs": pairs}], "unmatched": [{}] * unmatched}


def test_ic_points_count_pairs_over_tolerance_in_the_chosen_group():
    reads = {P06: _ic(over=2), P07: _ic(over=1, group="G2")}
    pts = M.ic_points([P06, P07], reads, "G1")
    assert pts[0]["value"] == 2
    assert pts[1]["value"] == 0  # G2's pair is not the chosen group's
    pts = M.ic_points([P06, P07], reads, None)
    assert [p["value"] for p in pts] == [2, 1]


def test_ic_points_unchecked_is_a_gap_naming_why():
    reads = {P07: {"state": "not_built", "message": "The warehouse is not built."}}
    (p,) = M.ic_points([P07], reads, None)
    assert p["value"] is None and p["note"] == "The warehouse is not built."


def test_ic_points_not_applicable_is_a_gap_saying_so():
    reads = {P07: {"state": "not_applicable", "message": "Declared not applicable."}}
    (p,) = M.ic_points([P07], reads, None)
    assert p["value"] is None and p["note"] == "Declared not applicable."


def test_ic_breakdown():
    assert M.ic_breakdown(_ic(over=1, within=2, unmatched=1), None) == (
        "1 of 3 pairs over tolerance · 1 without a partner")


def test_days_points_are_days_from_period_end_to_sign_off_and_unsigned_is_a_gap():
    states = [_state(P06, end="2025-06-30"), _state(P07, end="2025-07-31")]
    signed = {P06: {"signed_off_at": "2025-07-09 10:00:00"}}
    pts = M.days_points(states, signed)
    assert pts[0]["value"] == 9 and pts[0]["display"] == "9 days"
    assert pts[1]["value"] is None and "not signed off" in pts[1]["note"]


def test_result_points_take_the_net_result_series():
    chart = CM.chart(["FY2025 P06", "FY2025 P07"],
                     [("Net sales (EUR)", [100.0, 200.0]), ("Net result (EUR)", [1500.0, -2500000.0])])
    pts = M.result_points(chart, "EUR")
    assert [p["value"] for p in pts] == [1500.0, -2500000.0]
    assert pts[1]["display"] == "EUR -2.5M"
    assert M.compact(1500) == "1.5K" and M.compact(12) == "12"


# --- headline, banner, period card, entities, setup --------------------------------

def test_headline_counts_blocking_items():
    h = M.headline({"counts": {"blocking": 3, "todo": 2, "waiting": 1}})
    assert h["count"] == 3 and h["text"] == "3 items need you"
    assert h["detail"] == "2 to do · 1 waiting"
    assert h["route"].startswith("/desk/my-work")
    assert M.headline({"counts": {"blocking": 1, "todo": 0, "waiting": 0}})["text"] == "1 item needs you"
    assert M.headline({"counts": {"blocking": 0, "todo": 0, "waiting": 0}})["text"] == "Nothing needs you"


def test_headline_refuses_counts_it_cannot_read():
    with pytest.raises(ValueError):
        M.headline({"items": []})


def test_banner_shows_a_running_build_then_incomplete_setup():
    banners = M.banners({"state": "pending", "pending": 2}, {"done": 3, "total": 11})
    assert [b["kind"] for b in banners] == ["build", "setup"]
    assert "build" in banners[0]["text"].lower()
    assert banners[1]["text"] == "Setup 3 of 11 done"
    assert all(b["route"].startswith("/desk/") for b in banners)


def test_banner_is_empty_when_nothing_is_true():
    assert M.banners({"state": "fresh", "pending": 0}, {"done": 11, "total": 11}) == []
    # setup not readable by this role: no setup banner, never a guessed one
    assert M.banners({"state": "fresh", "pending": 0}, None) == []


def test_period_card_carries_status_checks_freshness_and_waiting():
    card = M.period_card(P07, _state(P07, checks="Amber"), {"state": "fresh",
                                                          "as_of": "2025-08-01T10:00:00"}, 4)
    assert card["title"] == "FY2025 P07"
    assert card["status"] == "Open"
    assert card["close"] == {"text": "FY2025 P07 · Amber", "tone": "warn"}
    assert card["freshness"]["tone"] == "ok"
    assert card["waiting"] == 4
    assert card["route"].startswith("/desk/")


def _entities():
    return [{"name": "A", "entity_name": "Alpha"}, {"name": "B", "entity_name": "Beta"},
            {"name": "C", "entity_name": "Gamma"}, {"name": "D", "entity_name": "Delta"}]


def test_entity_rows_show_tb_and_ic_status_and_open_first():
    progress = {"expected": ["A", "B", "C"], "missing": ["C"]}
    rows = M.entity_rows(P07, _entities(), progress, {"A"}, {"B"}, _ic(over=1), None, None)
    by = {r["entity"]: r for r in rows}
    assert by["A"]["tb"] == "submitted" and by["B"]["tb"] == "exception"
    assert by["C"]["tb"] == "missing" and by["D"]["tb"] == "not_expected"
    assert by["A"]["ic"] == "over" and by["B"]["ic"] == "over" and by["C"]["ic"] == "ok"
    assert by["D"]["ic"] == "none"
    assert by["C"]["open"] and by["A"]["open"] and not by["D"]["open"]
    assert [r["entity"] for r in rows][:3] == ["A", "B", "C"]  # open first, then code
    assert by["A"]["route"] == ("/desk/trial-balance-submission?data_area_id=A"
                                "&fiscal_year=2025&fiscal_period=7")


def test_entity_rows_cut_to_the_callers_entities():
    progress = {"expected": ["A", "B"], "missing": []}
    rows = M.entity_rows(P07, _entities(), progress, {"A", "B"}, set(), None, None, {"A"})
    assert [r["entity"] for r in rows] == ["A"]


def test_entity_rows_ic_unknown_when_intercompany_was_not_checked():
    progress = {"expected": ["A"], "missing": []}
    rows = M.entity_rows(P07, _entities()[:1], progress, {"A"}, set(),
                         {"state": "not_built", "message": "x"}, None, None)
    assert rows[0]["ic"] == "unknown"


def test_entity_rows_refuse_an_expected_entity_with_no_submission_or_exception():
    progress = {"expected": ["A"], "missing": []}
    with pytest.raises(ValueError):
        M.entity_rows(P07, _entities()[:1], progress, set(), set(), None, None, None)


def test_setup_pill_hides_at_complete():
    assert M.setup_pill({"done": 3, "total": 11})["hidden"] is False
    assert M.setup_pill({"done": 11, "total": 11})["hidden"] is True
    assert M.setup_pill({"done": 3, "total": 11})["route"] == "/desk/konsol-setup"
    assert M.setup_pill(None) is None

"""Period strips (Desk query reports): the pure logic behind the Year and
Period slider strips. No DOM, no frappe; the script runs under node.

Rules: the typed boxes can hold values a strip cannot show (a year before 1980,
period 14 or -1), so the
strip sits at the nearest end and flags out_of_range. Filters are remembered
per user; a route option beats the saved value; with nothing saved the report
opens on the latest period with data. A route that carries a fiscal year or
period ignores the saved period entirely (no mixing of a route year with a
saved period).
"""
import json
import os
import shutil
import subprocess

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(APP_DIR, "public", "js", "period_strip_model.js")

HARNESS = r"""
const [script, casesJson] = process.argv.slice(1);
const m = require(script);
const out = JSON.parse(casesJson).map(([fn, args]) => m[fn](...args));
console.log(JSON.stringify(out));
"""


def _run(*calls):
    node = shutil.which("node")
    assert node, "node is required to run the period strip model"
    assert os.path.exists(SCRIPT), "period_strip_model.js is missing"
    result = subprocess.run([node, "-e", HARNESS, SCRIPT, json.dumps(calls)],
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_clamp_and_step_stay_inside_the_strip_and_are_integers():
    out = _run(["clamp", [5, 1, 12]], ["clamp", [0, 1, 12]], ["clamp", [13, 1, 12]],
               ["step", [12, 1, 1, 12]], ["step", [1, -1, 1, 12]], ["step", [6, 1, 1, 12]],
               ["step", [1975, 1, 1980, 2045]], ["step", [2045, 1, 1980, 2045]])
    assert out == [5, 1, 12, 12, 1, 7, 1980, 2045]


def test_step_from_an_empty_value_lands_on_the_range():
    assert _run(["step", [None, 1, 1, 12]], ["step", ["", -1, 1, 12]]) == [1, 1]


def test_step_from_outside_the_range_never_moves_further_out_or_backwards():
    # typed P13 then next stays 13 (not 12); P0 then prev stays 0 (not 1);
    # moving inward lands on the nearest end.
    out = _run(["step", [13, 1, 1, 12]], ["step", [13, -1, 1, 12]],
               ["step", [0, -1, 1, 12]], ["step", [0, 1, 1, 12]],
               ["step", [-5, 1, 1, 12]], ["step", [2050, 1, 1980, 2045]],
               ["step", [2050, -1, 1980, 2045]], ["step", [1975, -1, 1980, 2045]])
    assert out == [13, 12, 0, 1, 1, 2050, 2045, 1975]


def test_strip_position_shows_the_value_or_the_nearest_end_and_flags_out_of_range():
    out = _run(["strip_position", [7, 1, 12]], ["strip_position", [13, 1, 12]],
               ["strip_position", [0, 1, 12]], ["strip_position", [1975, 1980, 2045]],
               ["strip_position", [2045, 1980, 2045]])
    assert out == [{"value": 7, "out_of_range": False},
                   {"value": 12, "out_of_range": True},
                   {"value": 1, "out_of_range": True},
                   {"value": 1980, "out_of_range": True},
                   {"value": 2045, "out_of_range": False}]


def test_year_marks_keep_declared_years_in_range_with_a_percent_position():
    (marks,) = _run(["year_marks", [[2025, 2023, 1970, 2023, 2050, 1980, 2045], 1980, 2045]])
    assert marks == [{"year": 1980, "percent": 0}, {"year": 2023, "percent": (2023 - 1980) / 65 * 100},
                     {"year": 2025, "percent": (2025 - 1980) / 65 * 100}, {"year": 2045, "percent": 100}]


def test_route_beats_saved_beats_latest_beats_defaults():
    (got,) = _run(["initial_filters", [{
        "route": {"entity": "E1"},
        "saved": {"entity": "E2", "basis": "Local"},
        "latest": {"fiscal_year": 2024, "fiscal_period": 12},
        "defaults": {"entity": "E0", "basis": "Group", "currency": "USD"}}]])
    assert got["values"] == {"entity": "E1", "basis": "Local", "currency": "USD",
                             "fiscal_year": 2024, "fiscal_period": 12}
    assert got["source"] == {"entity": "route", "basis": "saved", "currency": "defaults",
                             "fiscal_year": "latest", "fiscal_period": "latest"}


def test_saved_period_beats_latest():
    (got,) = _run(["initial_filters", [{
        "route": {}, "saved": {"fiscal_year": 2023, "fiscal_period": 3},
        "latest": {"fiscal_year": 2024, "fiscal_period": 12}, "defaults": {}}]])
    assert got["values"] == {"fiscal_year": 2023, "fiscal_period": 3}
    assert got["source"] == {"fiscal_year": "saved", "fiscal_period": "saved"}


def test_a_route_year_ignores_the_saved_period_and_latest_so_they_never_mix():
    (got,) = _run(["initial_filters", [{
        "route": {"fiscal_year": 2022}, "saved": {"fiscal_year": 2023, "fiscal_period": 3, "basis": "Local"},
        "latest": {"fiscal_year": 2024, "fiscal_period": 12}, "defaults": {"fiscal_period": 1}}]])
    assert got["values"] == {"fiscal_year": 2022, "fiscal_period": 1, "basis": "Local"}
    assert got["source"] == {"fiscal_year": "route", "fiscal_period": "defaults", "basis": "saved"}


def test_a_route_period_alone_also_drops_the_saved_year():
    (got,) = _run(["initial_filters", [{
        "route": {"fiscal_period": 6}, "saved": {"fiscal_year": 2023, "fiscal_period": 3},
        "latest": {"fiscal_year": 2024, "fiscal_period": 12}, "defaults": {"fiscal_year": 2020}}]])
    assert got["values"] == {"fiscal_year": 2020, "fiscal_period": 6}


def test_zero_is_a_value_but_empty_and_null_are_not():
    (got,) = _run(["initial_filters", [{
        "route": {"fiscal_period": 0, "entity": ""}, "saved": {"entity": "E2"},
        "latest": None, "defaults": {"fiscal_year": 2020}}]])
    assert got["values"] == {"fiscal_period": 0, "entity": "E2", "fiscal_year": 2020}
    assert got["source"]["entity"] == "saved"


def test_nothing_known_for_a_key_leaves_it_out():
    (got,) = _run(["initial_filters", [{"route": {}, "saved": None, "latest": None, "defaults": {}}]])
    assert got == {"values": {}, "source": {}}


def test_to_save_keeps_only_listed_keys_and_drops_empty_values():
    (got,) = _run(["to_save", [{"fiscal_year": 2024, "fiscal_period": 0, "entity": "", "basis": None,
                                "currency": "USD", "other": "x"},
                               ["fiscal_year", "fiscal_period", "entity", "basis", "missing"]]])
    assert got == {"fiscal_year": 2024, "fiscal_period": 0}


def test_period_label_reads_open_and_close_at_the_ends():
    out = _run(["period_label", [0]], ["period_label", ["0"]], ["period_label", [13]],
               ["period_label", [7]], ["period_label", [None]], ["period_label", [""]],
               ["period_label", [14]])
    assert out == ["Open", "Open", "Close", "7", "", "", "14"]

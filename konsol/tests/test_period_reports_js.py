"""Period reports (Desk query reports): the one entry point each period report
calls from its onload. Runs under node with a fake frappe, a fake
``frappe.query_report`` and a fake strip; the real report_filter_memory runs
against fake user settings.

What it pins (Deepak Pai, 10 Nov 2026): a link (route options) carrying any
remembered filter means saved filters are ignored; otherwise ONE
set_filter_value applies the start values, and only the ones that differ; the
filters are saved after each report run, listed keys only; the strip info is
fetched once per page.
"""
import json
import os
import shutil
import subprocess

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(APP_DIR, "public", "js", "period_reports.js")

HARNESS = r"""
const script = process.argv[1];
const sc = JSON.parse(process.argv[2]);
const lib = require(script);

const calls = { server: [], set: [], update: [], attach: 0, removed: 0, strip_refresh: 0 };
const store = {};
if (sc.saved) store["konsol report filters - " + sc.report] = { filters: sc.saved };
const frappe = {
  route_options: sc.frappe_route_options || null,
  call: () => { throw new Error("frappe.call would show a dialog on 403"); },
  after_ajax: () => Promise.resolve(),
  model: { user_settings: {
    get: (dt) => Promise.resolve(store[dt] || {}),
    update: (dt, s) => { calls.update.push([dt, s]); store[dt] = s; return Promise.resolve(s); },
  } },
};
const vals = Object.assign({}, sc.current);
let refresh_runs = 0;
function make_report(name) {
  const r = {
    report_name: name,
    _no_refresh: sc.no_refresh === undefined ? true : sc.no_refresh,
    get_filter_value: (k) => vals[k],
    get_filter_values: () => { const o = {}; for (const k in vals) if (vals[k] !== "" && vals[k] != null) o[k] = vals[k]; return o; },
    get_filter: (k) => (k in vals ? {} : undefined),
    set_filter_value(o) { calls.set.push({ values: o, no_refresh_before: r._no_refresh });
      r._no_refresh = false; Object.assign(vals, o); },
    refresh() { refresh_runs += 1; return Promise.resolve(); },
    refresh_report() { return "refreshed"; },
  };
  return r;
}
const strip = { attach: (report, info) => {
  if (report.__konsol_period_strips) return report.__konsol_period_strips;
  calls.attach += 1;
  const container = { parentNode: { removeChild: () => { calls.removed += 1; container.parentNode = null; } } };
  const h = { container, refresh: () => { calls.strip_refresh += 1; } };
  report.__konsol_period_strips = h; return h; } };

const fetch = (url) => { const [path, query] = url.split("?");
  calls.server.push([path.replace("/api/method/", ""), "GET", query || ""]);
  return sc.info_fails ? Promise.reject(new Error("boom"))
    : Promise.resolve({ ok: true, json: () => Promise.resolve({ message: sc.info }) }); };
const pr = lib.create({ frappe, fetch, strip, search: () => sc.search || "" });
const keys = sc.keys;
const tick = () => new Promise((r) => setTimeout(r, 0));
(async () => {
  console.warn = () => {};
  const report = make_report(sc.report);
  await pr.setup(report, { keys });
  const out = { no_refresh_after: report._no_refresh };
  out.values_after_setup = Object.assign({}, vals);
  if (sc.second_setup) await pr.setup(make_report(sc.report), { keys });
  if (sc.run) {
    Object.assign(vals, sc.run);
    await report.refresh();
    await tick();
  }
  if (sc.reload) { out.refresh_report = report.refresh_report(); }
  out.calls = calls;
  out.refresh_runs = refresh_runs;
  out.strips_attached = Boolean(report.__konsol_period_strips);
  console.log(JSON.stringify(out));
})().catch((e) => { console.error(e && e.stack || e); process.exit(1); });
"""

INFO = {"years": [2023, 2024], "latest": {"fiscal_year": 2024, "fiscal_period": 12},
        "year_min": 1980, "year_max": 2045, "period_min": 1, "period_max": 12}
WS_KEYS = ["fiscal_year", "fiscal_period", "consolidation_group", "entities", "dimension"]
TODAY = {"fiscal_year": 2026, "fiscal_period": 11, "consolidation_group": "", "entities": [],
         "dimension": ""}
BASE = {"report": "Consolidation Worksheet", "keys": WS_KEYS, "info": INFO, "current": TODAY}
SLOT = "konsol report filters - Consolidation Worksheet"


def _run(scenario):
    node = shutil.which("node")
    assert node, "node is required"
    assert os.path.exists(SCRIPT), "period_reports.js is missing"
    r = subprocess.run([node, "-e", HARNESS, SCRIPT, json.dumps(scenario)],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def test_without_a_route_saved_filters_apply_in_one_call():
    saved = {"fiscal_year": 2023, "fiscal_period": 6, "consolidation_group": "G1",
             "entities": ["A", "B"]}
    out = _run({**BASE, "saved": saved})
    assert len(out["calls"]["set"]) == 1
    assert out["calls"]["set"][0]["values"] == saved
    assert out["values_after_setup"]["entities"] == ["A", "B"]


def test_only_values_that_differ_are_set_and_none_means_no_call():
    out = _run({**BASE, "saved": {"fiscal_year": 2026, "fiscal_period": 3}})
    assert [c["values"] for c in out["calls"]["set"]] == [{"fiscal_period": 3}]
    out = _run({**BASE, "saved": {"fiscal_year": 2026, "fiscal_period": 11}})
    assert out["calls"]["set"] == []


def test_inside_onload_the_no_refresh_flag_is_kept():
    # Frappe's refresh_report runs onload with _no_refresh true and refreshes
    # itself afterwards; set_filter_value clears the flag, so setup restores it
    # (no second run, no "Filter missing" mid-route).
    out = _run({**BASE, "saved": {"fiscal_year": 2023}})
    assert out["no_refresh_after"] is True
    out = _run({**BASE, "saved": {"fiscal_year": 2023}, "no_refresh": False})
    assert out["no_refresh_after"] is False


def test_nothing_saved_opens_on_the_latest_period_with_data():
    out = _run(BASE)
    assert [c["values"] for c in out["calls"]["set"]] == [{"fiscal_year": 2024, "fiscal_period": 12}]


def test_a_route_with_a_remembered_filter_ignores_saved_and_sets_nothing():
    saved = {"fiscal_year": 2023, "fiscal_period": 6, "consolidation_group": "G1"}
    out = _run({**BASE, "saved": saved, "search": "?fiscal_year=2025&fiscal_period=2"})
    assert out["calls"]["set"] == []

def test_a_route_without_a_period_still_opens_on_the_latest_period_not_saved():
    saved = {"fiscal_year": 2023, "fiscal_period": 6, "consolidation_group": "G1"}
    for extra in ({"search": "?consolidation_group=G2"},
                  # frappe.route_options, when still set, counts as a route too
                  {"frappe_route_options": {"entities": "[\"X\"]"}}):
        out = _run({**BASE, "saved": saved, **extra})
        assert [c["values"] for c in out["calls"]["set"]] == [
            {"fiscal_year": 2024, "fiscal_period": 12}], extra


def test_a_route_without_a_remembered_filter_still_applies_saved():
    out = _run({**BASE, "saved": {"fiscal_year": 2023}, "search": "?prepared_report_name=x"})
    assert out["calls"]["set"][0]["values"]["fiscal_year"] == 2023


def test_saved_keys_outside_the_list_are_not_applied():
    out = _run({**BASE, "saved": {"fiscal_year": 2023, "heading": "H"}})
    assert out["calls"]["set"][0]["values"] == {"fiscal_year": 2023, "fiscal_period": 12}


def test_filters_are_saved_after_each_run_listed_keys_only():
    out = _run({**BASE, "saved": {"fiscal_year": 2023, "fiscal_period": 6},
                "run": {"fiscal_period": 7, "entities": ["A"], "other": "x"}})
    assert out["refresh_runs"] == 1
    assert out["calls"]["update"][-1] == [SLOT, {"filters": {
        "fiscal_year": 2023, "fiscal_period": 7, "entities": ["A"]}}]
    assert out["calls"]["strip_refresh"] >= 1


def test_strip_info_is_fetched_once_with_get_across_setups():
    out = _run({**BASE, "second_setup": True})
    assert out["calls"]["server"] == [["konsol.close.period_strip_api.period_strip_info", "GET", ""]]
    assert out["calls"]["attach"] == 2  # one per report object
    assert out["strips_attached"]


def test_info_failure_still_applies_saved_and_shows_no_strips():
    out = _run({**BASE, "info_fails": True, "saved": {"fiscal_year": 2023}})
    assert out["calls"]["set"][0]["values"] == {"fiscal_year": 2023}
    assert out["calls"]["attach"] == 0


def test_reloading_the_report_removes_the_strips_until_setup_runs_again():
    # The query-report page is one object for every report: another report
    # must not keep this report's strips.
    out = _run({**BASE, "reload": True})
    assert out["refresh_report"] == "refreshed"
    assert out["calls"]["removed"] == 1
    assert not out["strips_attached"]


def test_the_reports_consolidation_group_is_sent_to_the_endpoint():
    out = _run({**BASE, "current": {**TODAY, "consolidation_group": "G 9"}})
    assert out["calls"]["server"] == [["konsol.close.period_strip_api.period_strip_info", "GET",
                                       "consolidation_group=G+9"]]


def test_a_failed_info_call_is_not_retried_on_the_next_setup():
    out = _run({**BASE, "info_fails": True, "second_setup": True})
    assert len(out["calls"]["server"]) == 1
    assert out["calls"]["attach"] == 0

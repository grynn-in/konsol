"""Report filter memory (Desk query reports): remembered filters per user,
stored in Frappe user settings under a per-report doctype key. Runs under node
with a fake frappe that records calls.
"""
import json
import os
import shutil
import subprocess

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(APP_DIR, "public", "js", "report_filter_memory.js")

HARNESS = r"""
const [script, scenario] = process.argv.slice(1);
const mem = require(script);
function fake(store, opts = {}) {
  const calls = [];
  const frappe = { model: { user_settings: {
    get: (dt) => { calls.push(["get", dt]);
      return opts.fail_get ? Promise.reject(new Error("boom")) : Promise.resolve(store[dt] || {}); },
    update: (dt, s) => { calls.push(["update", dt, s]);
      if (opts.fail_update) return Promise.reject(new Error("boom"));
      store[dt] = Object.assign({}, store[dt] || {}, s); return Promise.resolve(s); },
  } } };
  return { frappe, calls };
}
const KEYS = ["fiscal_year", "fiscal_period", "entities"];
const scenarios = {
  async save_only_listed_nonempty() {
    const { frappe, calls } = fake({});
    const m = mem.create(frappe);
    await m.save("R", { fiscal_year: 2025, fiscal_period: "", entities: ["A"], other: 1 }, KEYS);
    return calls;
  },
  async unchanged_second_save() {
    const { frappe, calls } = fake({});
    const m = mem.create(frappe);
    await m.save("R", { fiscal_year: 2025 }, KEYS);
    await m.save("R", { fiscal_year: 2025, other: 2 }, KEYS);
    return calls.filter((c) => c[0] === "update").length;
  },
  async changed_second_save() {
    const { frappe, calls } = fake({});
    const m = mem.create(frappe);
    await m.save("R", { fiscal_year: 2025 }, KEYS);
    await m.save("R", { fiscal_year: 2026 }, KEYS);
    return calls.filter((c) => c[0] === "update").length;
  },
  async load_returns_saved() {
    const { frappe, calls } = fake({});
    const m = mem.create(frappe);
    await m.save("R", { fiscal_year: 2025 }, KEYS);
    const m2 = mem.create(frappe);
    return await m2.load("R");
  },
  async route_with_key_ignores_saved() {
    const { frappe } = fake({});
    const m = mem.create(frappe);
    await m.save("R", { fiscal_year: 2024, entities: ["A"] }, KEYS);
    const m2 = mem.create(frappe);
    return await m2.start_values({ report_name: "R", route_options: { entities: ["B"] },
      latest: { fiscal_year: 2025, fiscal_period: 3 }, defaults: {}, keys: KEYS });
  },
  async no_route_saved_beats_latest() {
    const { frappe } = fake({});
    const m = mem.create(frappe);
    await m.save("R", { fiscal_year: 2024 }, KEYS);
    const m2 = mem.create(frappe);
    return await m2.start_values({ report_name: "R", route_options: {},
      latest: { fiscal_year: 2025, fiscal_period: 3 }, defaults: {}, keys: KEYS });
  },
  async load_failure() {
    const { frappe } = fake({}, { fail_get: true });
    const m = mem.create(frappe);
    return [await m.load("R"), (await m.start_values({ report_name: "R", route_options: {},
      latest: { fiscal_year: 2025 }, defaults: {}, keys: KEYS })).values];
  },
  async save_failure_swallowed() {
    const { frappe } = fake({}, { fail_update: true });
    const m = mem.create(frappe);
    await m.save("R", { fiscal_year: 2025 }, KEYS);
    return "ok";
  },
  async no_user_settings() {
    const m = mem.create({});
    await m.save("R", { fiscal_year: 2025 }, KEYS);
    return [await m.load("R"), (await m.start_values({ report_name: "R", route_options: {},
      latest: { fiscal_year: 2025 }, defaults: {}, keys: KEYS })).values];
  },
};
const origErr = console.error, origWarn = console.warn;
console.error = console.warn = () => {};
scenarios[scenario]().then((r) => { console.error = origErr; console.warn = origWarn;
  console.log(JSON.stringify(r)); });
"""


def _run(scenario):
    node = shutil.which("node")
    assert node, "node is required"
    assert os.path.exists(SCRIPT), "report_filter_memory.js is missing"
    r = subprocess.run([node, "-e", HARNESS, SCRIPT, scenario],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


KEY = "konsol report filters - R"


def test_save_writes_only_listed_nonempty_keys():
    calls = _run("save_only_listed_nonempty")
    updates = [c for c in calls if c[0] == "update"]
    assert updates == [["update", KEY, {"filters": {"fiscal_year": 2025, "entities": ["A"]}}]]


def test_unchanged_second_save_makes_no_call_and_a_change_does():
    assert _run("unchanged_second_save") == 1
    assert _run("changed_second_save") == 2


def test_load_returns_saved():
    assert _run("load_returns_saved") == {"fiscal_year": 2025}


def test_route_with_any_key_ignores_saved_entirely():
    out = _run("route_with_key_ignores_saved")
    assert out["values"] == {"entities": ["B"], "fiscal_year": 2025, "fiscal_period": 3}
    assert out["source"]["entities"] == "route"
    assert out["source"]["fiscal_year"] == "latest"


def test_without_route_saved_beats_latest():
    out = _run("no_route_saved_beats_latest")
    assert out["values"]["fiscal_year"] == 2024
    assert out["source"]["fiscal_year"] == "saved"


def test_failures_never_break_the_report():
    assert _run("load_failure") == [{}, {"fiscal_year": 2025}]
    assert _run("save_failure_swallowed") == "ok"
    assert _run("no_user_settings") == [{}, {"fiscal_year": 2025}]

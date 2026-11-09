"""konsol#497 row 2: the Konsol Home Tabs block skeleton.

Decision (Deepak Pai, 9 Nov 2026, #497): option (d), frappe.ui.Tabs in a Custom
HTML Block; tabs Close / Approvals / Trends / Numbers, Close the default.
The skeleton builds the tabs with lazy, empty panels and asks the server for
nothing at construction. The script runs under node against a stub frappe.
"""
import json
import os
import shutil
import subprocess

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FOLDER = os.path.join(APP_DIR, "consolidation", "custom_html_block", "konsol_home_tabs")
SCRIPT = os.path.join(FOLDER, "konsol_home_tabs.js")

HARNESS = r"""
const fs = require("fs");
const [script, cfgJson] = process.argv.slice(1);
const cfg = JSON.parse(cfgJson || "{}");
const store = {};
if ("stored" in cfg) store["konsol-home-tab:me@example.com"] = cfg.stored;
const storeCalls = { get: 0, set: [], other: 0 };
const listeners = [];
function node(tag) {
  return { tag, className: "", children: [], textContent: "",
    appendChild(c) { this.children.push(c); return c; },
    append(...c) { c.forEach((x) => this.children.push(x)); },
    addEventListener(t) { listeners.push(t); },
    querySelector(sel) { return sel === ".konsol-home-tabs" ? box : null; } };
}
const box = node("section");
const calls = [];
const created = [];
global.document = { createElement: (t) => node(t), addEventListener: (t) => listeners.push(t) };
global.localStorage = {
  getItem(k) { storeCalls.get++; return k in store ? store[k] : null; },
  setItem(k, v) { storeCalls.set.push([k, v]); store[k] = String(v); },
};
global.root_element = node("root");
global.frappe = {
  session: { user: "me@example.com" },
  xcall: (...a) => { calls.push(["xcall", a]); return Promise.resolve({}); },
  call: (...a) => { calls.push(["call", a]); return Promise.resolve({}); },
  ui: { Tabs: function (opts) { created.push(opts); this.opts = opts; this.$el = node("tabs"); } },
};
eval(fs.readFileSync(script, "utf8"));
const opts = created[0];
const afterConstruct = { gets: storeCalls.get, sets: storeCalls.set.length, calls: calls.length };
if ("click" in cfg) opts.on_change(cfg.click, opts.tabs[cfg.click]);
const panels = opts.tabs.map((t) => (typeof t.content === "function" ? t.content() : t.content));
console.log(JSON.stringify({
  tabs_built: created.length,
  labels: opts.tabs.map((t) => t.label),
  active: opts.active,
  content_is_function: opts.tabs.map((t) => typeof t.content === "function"),
  panel_children: panels.map((p) => (p && p.children ? p.children.length : -1)),
  calls: calls.length,
  mounted: box.children.length,
  has_on_change: typeof opts.on_change,
  after_construct: afterConstruct,
  sets: storeCalls.set,
  calls_after_click: calls.length,
  listeners,
}));
"""


def _run(cfg=None):
    node = shutil.which("node")
    assert node, "node is required to run the Konsol Home Tabs script"
    assert os.path.exists(SCRIPT), "konsol_home_tabs.js (the home tabs block) is missing"
    result = subprocess.run([node, "-e", HARNESS, SCRIPT, json.dumps(cfg or {})], capture_output=True,
                            text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_the_block_ships_html_js_and_css():
    for ext in ("html", "js", "css"):
        assert os.path.exists(os.path.join(FOLDER, "konsol_home_tabs.%s" % ext))
    with open(os.path.join(FOLDER, "konsol_home_tabs.html")) as f:
        assert 'class="konsol-home-tabs"' in f.read()


def test_tabs_are_close_approvals_trends_numbers_with_close_active():
    out = _run()
    assert out["tabs_built"] == 1
    assert out["labels"] == ["Close", "Approvals", "Trends", "Numbers"]
    assert out["active"] == 0
    assert out["mounted"] == 1


def test_content_is_lazy_and_panels_start_empty():
    out = _run()
    assert out["content_is_function"] == [True, True, True, True]
    assert out["panel_children"] == [0, 0, 0, 0]


def test_construction_makes_no_server_call():
    assert _run()["calls"] == 0


KEY = "konsol-home-tab:me@example.com"


def test_stored_index_becomes_the_active_tab():
    assert _run({"stored": "2"})["active"] == 2
    assert _run({"stored": "3"})["active"] == 3


def test_garbage_out_of_range_or_missing_falls_back_to_close():
    for bad in ("abc", "", "-1", "4", "1.5", "99", "null", "[2]"):
        assert _run({"stored": bad})["active"] == 0, bad
    assert _run({})["active"] == 0


def test_one_change_makes_exactly_one_store():
    out = _run({"click": 2})
    assert out["sets"] == [[KEY, "2"]]
    assert out["calls_after_click"] == 0


def test_construction_makes_no_store_write_and_no_server_call():
    out = _run({"stored": "2"})
    assert out["after_construct"]["sets"] == 0
    assert out["after_construct"]["calls"] == 0


def test_no_change_or_input_listeners_are_registered():
    out = _run({"stored": "1", "click": 3})
    assert "change" not in out["listeners"] and "input" not in out["listeners"]
    with open(SCRIPT) as f:
        src = f.read()
    assert "addEventListener" not in src or ('"change"' not in src and '"input"' not in src)
    assert ".on(\"change\"" not in src

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
const callsAfterClick = calls.length;
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
  calls_after_click: callsAfterClick,
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


# ---- row 4A: Close panel, cards and Monthly close shortcuts ----

HARNESS_4A = r"""
const fs = require("fs");
const [script, cfgJson] = process.argv.slice(1);
const cfg = JSON.parse(cfgJson || "{}");
const handlers = {};
function node(tag) {
  return { tag, className: "", children: [], textContent: "",
    appendChild(c) { this.children.push(c); return c; },
    append(...c) { c.forEach((x) => this.children.push(x)); },
    addEventListener(t, fn) { (handlers[t] = handlers[t] || []).push(fn); },
    querySelector(sel) { return sel === ".konsol-home-tabs" ? box : null; } };
}
const box = node("section");
const calls = [];
const groups = [];
const rafQueue = [];
const routes = [];
const created = [];
global.document = { createElement: (t) => node(t), addEventListener() {} };
global.localStorage = { getItem() { return null; }, setItem() {} };
global.location = { href: "https://site.test/desk/konsol", origin: "https://site.test" };
global.requestAnimationFrame = (fn) => { rafQueue.push(fn); return rafQueue.length; };
global.$ = (x) => x;
global.root_element = node("root");
const card = (n) => ({ label: n, name: n });
const sc = (n) => ({ label: n });
const defs = cfg.defs || {
  number_cards: { items: ["Close Status", "Trial Balances In", "Waiting for My Approval",
    "IC Pairs over Tolerance", "TB Exceptions Declared", "Warehouse Freshness"].map(card) },
  shortcuts: { items: ["Consolidation Journals", "Close Assertions", "My Work", "Build Approvals",
    "Period Close", "Consolidated Statement", "Trial Balance Uploads", "Trial Balance Submissions"].map(sc) },
};
global.frappe = {
  session: { user: "me@example.com" },
  xcall: (...a) => {
    calls.push(a);
    return cfg.reject ? Promise.reject(new Error(cfg.reject)) : Promise.resolve(defs);
  },
  set_route: (...a) => routes.push(a),
  widget: { WidgetGroup: function (o) { groups.push(o); } },
  ui: { Tabs: function (opts) { created.push(opts); this.$el = node("tabs"); } },
};
eval(fs.readFileSync(script, "utf8"));
const opts = created[0];
const atConstruct = calls.length;
const tick = () => new Promise((r) => setImmediate(r));
(async () => {
  const panels = [];
  for (const i of (cfg.activate || [0])) panels.push(opts.tabs[i].content());
  await tick(); await tick();
  const beforeRaf = { groups: groups.length, raf: rafQueue.length };
  rafQueue.splice(0).forEach((f) => f());
  await tick();
  const out = {
    at_construct: atConstruct, calls: calls.length, call_args: calls[0] || null,
    before_raf: beforeRaf,
    groups: groups.map((g) => ({ type: g.type, columns: g.columns,
      labels: g.widgets.map((w) => w.label) })),
    panel_text: panels.map((p) => p.children.map((c) => c.textContent)),
  };
  if (cfg.link) {
    let prevented = 0;
    const a = { getAttribute: () => cfg.link, href: new URL(cfg.link, location.href).href };
    const ev = Object.assign({ button: 0, preventDefault() { prevented++; },
      target: { closest: () => a } }, cfg.mods || {});
    (handlers.click || []).forEach((h) => h(ev));
    out.click = { handlers: (handlers.click || []).length, prevented, routes };
  }
  console.log(JSON.stringify(out));
})();
"""

MONTHLY = ["My Work", "Period Close", "Consolidated Statement", "Trial Balance Uploads",
           "Trial Balance Submissions", "Close Assertions"]
CARDS = ["Close Status", "Trial Balances In", "Waiting for My Approval",
         "IC Pairs over Tolerance", "TB Exceptions Declared", "Warehouse Freshness"]


def _run4a(cfg=None):
    node = shutil.which("node")
    assert node
    result = subprocess.run([node, "-e", HARNESS_4A, SCRIPT, json.dumps(cfg or {})],
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_close_panel_shows_six_cards_in_band_order():
    out = _run4a()
    cards = [g for g in out["groups"] if g["type"] == "number_card"]
    assert len(cards) == 1 and cards[0]["labels"] == CARDS


def test_close_panel_shows_the_monthly_close_shortcuts_in_order():
    out = _run4a()
    shortcuts = [g for g in out["groups"] if g["type"] == "shortcut"]
    assert len(shortcuts) == 1 and shortcuts[0]["labels"] == MONTHLY


def test_shortcuts_the_user_may_not_see_are_simply_absent():
    defs = {"number_cards": {"items": []},
            "shortcuts": {"items": [{"label": "Period Close"}, {"label": "My Work"}]}}
    out = _run4a({"defs": defs})
    shortcuts = [g for g in out["groups"] if g["type"] == "shortcut"]
    assert shortcuts[0]["labels"] == ["My Work", "Period Close"]


def test_one_definitions_call_even_when_two_panels_run():
    out = _run4a({"activate": [0, 1]})
    assert out["calls"] == 1
    assert out["call_args"][0] == "frappe.desk.desktop.get_desktop_page"
    page = json.loads(out["call_args"][1]["page"])
    assert page["name"] == "Konsol"
    assert out["call_args"][2] == "GET"


def test_no_definitions_call_at_construction():
    assert _run4a()["at_construct"] == 0


def test_widgets_are_not_created_before_the_animation_frame():
    out = _run4a()
    assert out["before_raf"]["groups"] == 0
    assert out["before_raf"]["raf"] >= 1
    assert len(out["groups"]) == 2


def test_in_app_anchor_click_routes_through_frappe_and_is_prevented():
    for href, route in (("/desk/build-approval", ["build-approval"]),
                        ("/desk/List/Build%20Approval?x=1", ["List", "Build Approval"]),
                        ("/app/period-close", ["period-close"])):
        out = _run4a({"link": href})
        assert out["click"]["handlers"] == 1, href
        assert out["click"]["prevented"] == 1, href
        assert out["click"]["routes"] == [route], href


def test_external_and_non_desk_links_are_left_alone():
    for href in ("https://example.com/desk/x", "/api/method/ping", "mailto:a@b.c", "#top"):
        out = _run4a({"link": href})
        assert out["click"]["prevented"] == 0, href
        assert out["click"]["routes"] == [], href


def test_modified_clicks_are_left_to_the_browser():
    out = _run4a({"link": "/desk/build-approval", "mods": {"ctrlKey": True}})
    assert out["click"]["prevented"] == 0 and out["click"]["routes"] == []


def test_rejected_definitions_call_shows_an_error_line_not_an_empty_panel():
    out = _run4a({"reject": "boom"})
    assert out["groups"] == []
    lines = out["panel_text"][0]
    assert len(lines) == 1 and "boom" in lines[0] and "Cannot read" in lines[0]

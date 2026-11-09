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
  return { tag, className: "", children: [], textContent: "", style: {},
    setAttribute() {}, replaceChildren(...c) { this.children = c; },
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
    # Close holds its four sections (headline, cards, checklist, Monthly close); Numbers its four links; Approvals and Trends fill after the definitions call.
    assert out["panel_children"] == [4, 0, 0, 4]


def test_construction_makes_no_server_call():
    assert _run()["after_construct"]["calls"] == 0


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
  return { tag, className: "", children: [], textContent: "", style: {},
    setAttribute() {}, replaceChildren(...c) { this.children = c; },
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
    if (a[0] === "konsol.close.overview_api.get_checklist") {
      return Promise.resolve({ title: "x", done: 0, total: 0, lines: [] });
    }
    if (a[0] === "konsol.close.mywork_api.get_my_work") {
      return Promise.resolve({ items: [], counts: { blocking: 0, todo: 0, waiting: 0 } });
    }
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
    lines = [t for t in out["panel_text"][0] if t]
    assert len(lines) == 1 and "boom" in lines[0] and "Cannot read" in lines[0]


# ---- row 5: Close panel, checklist ----

HARNESS_5 = r"""
const fs = require("fs");
const [script, cfgJson] = process.argv.slice(1);
const cfg = JSON.parse(cfgJson || "{}");
const htmlSets = [];
function node(tag) {
  const n = { tag, className: "", children: [], textContent: "", style: {}, attrs: {}, id: "",
    setAttribute(k, v) { this.attrs[k] = v; },
    replaceChildren(...c) { this.children = c; },
    appendChild(c) { this.children.push(c); return c; },
    append(...c) { c.forEach((x) => this.children.push(x)); },
    addEventListener() {},
    querySelector(sel) { return sel === ".konsol-home-tabs" ? box : null; } };
  for (const k of ["innerHTML", "outerHTML"]) {
    Object.defineProperty(n, k, { set(v) { htmlSets.push(k); }, get() { return ""; } });
  }
  return n;
}
const box = node("section");
const calls = [];
const rafQueue = [];
const created = [];
global.document = { createElement: (t) => node(t), addEventListener() {} };
global.localStorage = { getItem() { return null; }, setItem() {} };
global.location = { href: "https://site.test/desk/konsol", origin: "https://site.test" };
global.requestAnimationFrame = (fn) => { rafQueue.push(fn); return 1; };
global.$ = (x) => x;
global.root_element = node("root");
const payload = cfg.payload || {
  title: "FY2025 P07", done: 2, total: 4, reason: "",
  lines: [
    { state: "ok", label: "TBs in", detail: "41 of 41", route: "/desk/trial-balance-submission" },
    { state: "warn", label: "IC", detail: "3 over", route: "https://evil.example/x" },
    { state: "blocked", label: "Approvals", detail: "2 waiting", route: null },
    { state: "unknown", label: "Freshness", detail: "?", route: "/desk/build-approval" },
  ],
};
global.frappe = {
  session: { user: "me@example.com" },
  xcall: (...a) => {
    calls.push(a);
    if (a[0] === "konsol.close.overview_api.get_checklist") {
      return cfg.reject ? Promise.reject(new Error(cfg.reject)) : Promise.resolve(payload);
    }
    return Promise.resolve({ number_cards: { items: [] }, shortcuts: { items: [] } });
  },
  set_route() {},
  widget: { WidgetGroup: function () {} },
  ui: { Tabs: function (opts) { created.push(opts); this.$el = node("tabs"); } },
};
eval(fs.readFileSync(script, "utf8"));
const opts = created[0];
const isCl = (c) => c[0] === "konsol.close.overview_api.get_checklist";
const atConstruct = calls.filter(isCl).length;
const tick = () => new Promise((r) => setImmediate(r));
function walk(n, f) { f(n); (n.children || []).forEach((c) => walk(c, f)); }
(async () => {
  const closes = [];
  const n = cfg.activations || 1;
  for (let i = 0; i < n; i++) closes.push(opts.tabs[0].content());
  if (cfg.other) opts.tabs[1].content();
  await tick(); await tick();
  rafQueue.splice(0).forEach((f) => f());
  await tick(); await tick();
  const panel = closes[0];
  const order = panel.children.map((c) => c.className);
  let boxNode = null;
  walk(panel, (x) => { if ((x.className || "").split(" ").includes("khp-checklist")) boxNode = x; });
  const info = { at_construct: atConstruct, checklist_calls: calls.filter(isCl).length,
    call: calls.find(isCl) || null, order, html_sets: htmlSets, found: !!boxNode };
  if (boxNode) {
    const cls = (x, c) => (x.className || "").split(" ").includes(c);
    const texts = [], lines = [], hrefs = [], errors = [];
    let bar = null, fill = null, count = null;
    walk(boxNode, (x) => {
      if (cls(x, "kc-error")) errors.push(x.textContent);
      if (cls(x, "kc-count")) count = x.textContent;
      if (cls(x, "kc-progress")) bar = x.attrs;
      if (cls(x, "kc-progress-fill")) fill = x.style.width;
      if (cls(x, "kc-title")) texts.push(x.textContent);
      if (cls(x, "kc-line")) {
        const parts = [];
        walk(x, (y) => { if (y !== x && y.textContent) parts.push(y.textContent); });
        lines.push(parts.join(" | "));
      }
      if (cls(x, "kc-label") && x.href) hrefs.push(x.href);
    });
    Object.assign(info, { title: texts[0] || null, count, bar, fill, lines, hrefs, errors });
  }
  console.log(JSON.stringify(info));
})();
"""


def _run5(cfg=None):
    node = shutil.which("node")
    assert node
    result = subprocess.run([node, "-e", HARNESS_5, SCRIPT, json.dumps(cfg or {})],
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_checklist_sits_between_the_cards_and_the_monthly_close_shortcuts():
    out = _run5()
    assert out["found"]
    cls = [c.split()[0] if c else "" for c in out["order"]]
    assert cls.index("khp-cards") < cls.index("khp-checklist") < cls.index("khp-monthly")


def test_checklist_renders_title_lines_and_progress_from_the_payload():
    out = _run5()
    assert out["title"] == "Period close \u00b7 FY2025 P07"
    assert out["count"] == "2 of 4 done"
    assert out["fill"] == "50%"
    assert out["bar"]["aria-valuenow"] == "2" and out["bar"]["aria-valuemax"] == "4"
    assert out["lines"] == ["\u2713 | TBs in | 41 of 41", "! | IC | 3 over",
                            "\u2715 | Approvals | 2 waiting", "? | Freshness | ?"]


def test_checklist_links_only_to_desk_routes():
    out = _run5()
    assert out["hrefs"] == ["/desk/trial-balance-submission", "/desk/build-approval"]


def test_checklist_progress_is_clamped():
    payload = {"title": "P", "done": 9, "total": 4, "reason": "", "lines": []}
    out = _run5({"payload": payload})
    assert out["count"] == "4 of 4 done" and out["fill"] == "100%"


def test_rejected_checklist_call_shows_an_error_line_and_sets_no_html():
    out = _run5({"reject": "<b>boom</b>"})
    assert out["errors"] == ["The period close checklist could not be read: <b>boom</b>"]
    assert out["html_sets"] == []


def test_unknown_checklist_state_shows_an_error_line():
    payload = {"title": "P", "done": 0, "total": 1, "reason": "",
               "lines": [{"state": "weird", "label": "x", "detail": "y", "route": None}]}
    out = _run5({"payload": payload})
    assert len(out["errors"]) == 1 and "weird" in out["errors"][0]


def test_checklist_is_not_called_at_construction_and_once_per_close_activation():
    out = _run5()
    assert out["at_construct"] == 0
    assert out["checklist_calls"] == 1
    assert out["call"][1] == {} and out["call"][2] == "GET"
    assert _run5({"other": True})["checklist_calls"] == 1


def test_no_new_click_listener_is_added_for_the_checklist():
    with open(SCRIPT) as f:
        src = f.read()
    assert src.count("addEventListener(") == 1
    assert "innerHTML" not in src


# ---- row 6: Close panel, #484 headline ----

HARNESS_6 = r"""
const fs = require("fs");
const [script, cfgJson] = process.argv.slice(1);
const cfg = JSON.parse(cfgJson || "{}");
const handlers = {};
function node(tag) {
  return { tag, className: "", children: [], textContent: "", style: {}, attrs: {}, href: "",
    setAttribute(k, v) { this.attrs[k] = v; },
    replaceChildren(...c) { this.children = c; },
    appendChild(c) { this.children.push(c); return c; },
    append(...c) { c.forEach((x) => this.children.push(x)); },
    addEventListener(t, fn) { (handlers[t] = handlers[t] || []).push(fn); },
    querySelector(sel) { return sel === ".konsol-home-tabs" ? box : null; } };
}
const box = node("section");
const calls = [];
const routes = [];
const rafQueue = [];
const created = [];
global.document = { createElement: (t) => node(t), addEventListener() {} };
global.localStorage = { getItem() { return null; }, setItem() {} };
global.location = { href: "https://site.test/desk/konsol", origin: "https://site.test" };
global.requestAnimationFrame = (fn) => { rafQueue.push(fn); return 1; };
global.$ = (x) => x;
global.root_element = node("root");
const MW = "konsol.close.mywork_api.get_my_work";
global.frappe = {
  session: { user: "me@example.com" },
  xcall: (...a) => {
    calls.push(a);
    if (a[0] === MW) {
      return cfg.reject ? Promise.reject(new Error(cfg.reject)) : Promise.resolve(cfg.work);
    }
    if (a[0] === "konsol.close.overview_api.get_checklist") {
      return Promise.resolve({ title: "x", done: 0, total: 0, lines: [] });
    }
    return Promise.resolve({ number_cards: { items: [] }, shortcuts: { items: [] } });
  },
  set_route: (...a) => routes.push(a),
  widget: { WidgetGroup: function () {} },
  ui: { Tabs: function (opts) { created.push(opts); this.$el = node("tabs"); } },
};
eval(fs.readFileSync(script, "utf8"));
const opts = created[0];
const isMW = (c) => c[0] === MW;
const atConstruct = calls.filter(isMW).length;
const tick = () => new Promise((r) => setImmediate(r));
function walk(n, f) { f(n); (n.children || []).forEach((c) => walk(c, f)); }
const cls = (x, c) => (x.className || "").split(" ").includes(c);
(async () => {
  const closes = [];
  for (let i = 0; i < (cfg.activations || 1); i++) closes.push(opts.tabs[0].content());
  if (cfg.other) opts.tabs[1].content();
  await tick(); await tick();
  rafQueue.splice(0).forEach((f) => f());
  await tick(); await tick();
  const panel = closes[0];
  let head = null;
  walk(panel, (x) => { if (cls(x, "khp-headline")) head = x; });
  const info = { at_construct: atConstruct, mw_calls: calls.filter(isMW).length,
    call: calls.find(isMW) || null, first_class: (panel.children[0] || {}).className || "",
    found: !!head };
  if (head) {
    const text = [], detail = [], errors = [], starts = [];
    walk(head, (x) => {
      if (cls(x, "khp-headline-text")) text.push(x.textContent);
      if (cls(x, "khp-headline-detail")) detail.push(x.textContent);
      if (cls(x, "khp-error")) errors.push(x.textContent);
      if (cls(x, "khp-start")) starts.push({ text: x.textContent, href: x.href });
    });
    let all = "";
    walk(head, (x) => { all += " " + x.textContent; });
    Object.assign(info, { text, detail, errors, starts, all });
    if (starts[0]) {
      let prevented = 0;
      const a = { getAttribute: () => starts[0].href, href: new URL(starts[0].href, location.href).href };
      const ev = { button: 0, preventDefault() { prevented++; }, target: { closest: () => a } };
      (handlers.click || []).forEach((h) => h(ev));
      info.click = { prevented, routes };
    }
  }
  console.log(JSON.stringify(info));
})();
"""


def _work(blocking=None, todo=2, waiting=3, counts=True):
    c = {"todo": todo, "waiting": waiting}
    if blocking is not None:
        c["blocking"] = blocking
    return {"items": [], "counts": c} if counts else {"items": []}


def _run6(cfg=None):
    node = shutil.which("node")
    assert node
    result = subprocess.run([node, "-e", HARNESS_6, SCRIPT, json.dumps(cfg or {})],
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_headline_text_for_zero_one_and_many_blocking_items():
    assert _run6({"work": _work(0)})["text"] == ["Nothing needs you"]
    assert _run6({"work": _work(1)})["text"] == ["1 item needs you"]
    assert _run6({"work": _work(5)})["text"] == ["5 items need you"]


def test_headline_detail_counts_todo_and_waiting():
    out = _run6({"work": _work(2, todo=4, waiting=7)})
    assert out["detail"] == ["4 to do · 7 waiting"]


def test_headline_is_the_first_section_of_the_close_panel():
    out = _run6({"work": _work(1)})
    assert out["found"] and out["first_class"].split()[0] == "khp-headline"


def test_missing_counts_or_blocking_gives_an_error_line_never_zero():
    for work in (_work(counts=False), _work(blocking=None), {"items": [], "counts": None}):
        out = _run6({"work": work})
        assert len(out["errors"]) == 1 and out["errors"][0].startswith("Cannot read"), work
        assert out["text"] == [] and out["starts"] == [], work
        assert "Nothing needs you" not in out["all"] and "0" not in out["all"], work


def test_rejected_my_work_call_shows_an_error_line():
    out = _run6({"reject": "boom"})
    assert len(out["errors"]) == 1 and "boom" in out["errors"][0]
    assert out["text"] == []


def test_my_work_is_not_called_at_construction_and_once_per_close_activation():
    out = _run6({"work": _work(1)})
    assert out["at_construct"] == 0
    assert out["mw_calls"] == 1
    assert out["call"][1] == {} and out["call"][2] == "GET"
    assert _run6({"work": _work(1), "other": True})["mw_calls"] == 1


def test_start_link_opens_my_work_through_the_existing_click_handler():
    out = _run6({"work": _work(3)})
    assert [s["text"] for s in out["starts"]] == ["Start"]
    assert out["starts"][0]["href"] == "/desk/query-report/My%20Work"
    assert out["click"]["prevented"] == 1
    assert out["click"]["routes"] == [["query-report", "My Work"]]


# ---- row 7: Approvals panel, the waiting shortcuts ----

WAITING = ["Consolidation Journals", "Business Combinations", "Business Disposals",
           "Group Exchange Rates", "Ownership Periods", "Historical Equity Rates",
           "IC Balances", "Build Approvals"]


def _defs7(labels):
    return {"number_cards": {"items": []},
            "shortcuts": {"items": [{"label": n} for n in labels]}}


def test_approvals_panel_shows_the_eight_waiting_shortcuts_in_workspace_order():
    # Definitions arrive in a scrambled order, with Monthly close shortcuts mixed in.
    scrambled = list(reversed(WAITING)) + ["Period Close", "My Work"]
    out = _run4a({"defs": _defs7(scrambled), "activate": [1]})
    shortcuts = [g for g in out["groups"] if g["type"] == "shortcut"]
    assert len(shortcuts) == 1 and shortcuts[0]["labels"] == WAITING


def test_waiting_labels_match_the_workspace_file():
    path = os.path.join(APP_DIR, "consolidation", "workspace", "konsol", "konsol.json")
    with open(path) as f:
        ws = json.load(f)
    declared = [s["label"] for s in ws["shortcuts"] if "waiting" in (s.get("format") or "")]
    assert declared == WAITING


def test_waiting_shortcuts_the_user_may_not_see_are_absent():
    have = ["Build Approvals", "Business Disposals", "Group Exchange Rates"]
    out = _run4a({"defs": _defs7(have), "activate": [1]})
    shortcuts = [g for g in out["groups"] if g["type"] == "shortcut"]
    assert shortcuts[0]["labels"] == ["Business Disposals", "Group Exchange Rates", "Build Approvals"]


def test_approvals_does_not_repeat_the_close_panel_shortcuts():
    out = _run4a({"defs": _defs7(WAITING + MONTHLY), "activate": [0, 1]})
    shortcuts = [g["labels"] for g in out["groups"] if g["type"] == "shortcut"]
    assert sorted(map(tuple, shortcuts)) == sorted([tuple(MONTHLY), tuple(WAITING)])


def test_one_definitions_call_when_close_and_approvals_both_activate():
    out = _run4a({"defs": _defs7(WAITING + MONTHLY), "activate": [0, 1]})
    assert out["calls"] == 1


def test_approvals_makes_nothing_before_activation_or_before_the_frame():
    out = _run4a({"defs": _defs7(WAITING), "activate": []})
    assert out["calls"] == 0 and out["groups"] == []
    out = _run4a({"defs": _defs7(WAITING), "activate": [1]})
    assert out["calls"] == 1
    assert out["before_raf"]["groups"] == 0 and out["before_raf"]["raf"] >= 1
    assert len(out["groups"]) == 1


def test_approvals_rejected_definitions_show_the_error_line():
    out = _run4a({"reject": "boom", "activate": [1]})
    assert out["groups"] == []
    lines = [t for t in out["panel_text"][0] if t]
    assert len(lines) == 1 and "Cannot read: boom" in lines[0]


# ---- row 8: Trends panel, the seven charts ----


def _workspace_charts():
    """(label, columns) per chart row in konsol.json, in order. The content holds
    only the tabs block (#497 row 11); TB Submission Progress is the full-width
    chart (columns 1), the rest are half (columns 2)."""
    path = os.path.join(APP_DIR, "consolidation", "workspace", "konsol", "konsol.json")
    with open(path) as f:
        ws = json.load(f)
    return [(c["chart_name"], 1 if c["chart_name"] == "TB Submission Progress" else 2)
            for c in ws["charts"]]


def _defs8(labels):
    return {"number_cards": {"items": []}, "shortcuts": {"items": []},
            "charts": {"items": [{"chart_name": n, "label": n} for n in labels]}}


def test_trends_panel_shows_the_charts_in_workspace_order_and_widths():
    declared = _workspace_charts()
    assert len(declared) == 7 and declared[0] == ("TB Submission Progress", 1)
    names = [n for n, _ in declared]
    scrambled = list(reversed(names))
    out = _run4a({"defs": _defs8(scrambled), "activate": [2]})
    charts = [g for g in out["groups"] if g["type"] == "chart"]
    # TB Submission Progress alone in a one-column group, first; the other six in a two-column group.
    assert [(g["columns"], g["labels"]) for g in charts] == [
        (1, [names[0]]),
        (2, names[1:]),
    ]
    for (n, cols), g in zip(declared, [charts[0]] + [charts[1]] * 6):
        assert g["columns"] == cols, n


def test_charts_the_user_may_not_see_are_absent():
    names = [n for n, _ in _workspace_charts()]
    out = _run4a({"defs": _defs8([names[3], names[1]]), "activate": [2]})
    charts = [g for g in out["groups"] if g["type"] == "chart"]
    assert [(g["columns"], g["labels"]) for g in charts] == [(2, [names[1], names[3]])]
    out = _run4a({"defs": _defs8([names[0], names[5]]), "activate": [2]})
    charts = [g for g in out["groups"] if g["type"] == "chart"]
    assert [(g["columns"], g["labels"]) for g in charts] == [(1, [names[0]]), (2, [names[5]])]


def test_one_definitions_call_when_close_approvals_and_trends_activate():
    names = [n for n, _ in _workspace_charts()]
    out = _run4a({"defs": _defs8(names), "activate": [0, 1, 2]})
    assert out["calls"] == 1


def test_trends_makes_nothing_before_activation_or_before_the_frame():
    names = [n for n, _ in _workspace_charts()]
    out = _run4a({"defs": _defs8(names), "activate": []})
    assert out["calls"] == 0 and out["groups"] == []
    out = _run4a({"defs": _defs8(names), "activate": [2]})
    assert out["calls"] == 1
    assert out["before_raf"]["groups"] == 0 and out["before_raf"]["raf"] >= 1
    assert len([g for g in out["groups"] if g["type"] == "chart"]) == 2


def test_trends_rejected_definitions_show_the_error_line():
    out = _run4a({"reject": "boom", "activate": [2]})
    assert out["groups"] == []
    lines = [t for t in out["panel_text"][0] if t]
    assert len(lines) == 1 and "Cannot read: boom" in lines[0]


# ---- row 9: Numbers panel, four report links ----

HARNESS_9 = r"""
const fs = require("fs");
const [script, cfgJson] = process.argv.slice(1);
const cfg = JSON.parse(cfgJson || "{}");
const handlers = {};
const events = [];
function node(tag) {
  const n = { tag, className: "", children: [], textContent: "", style: {}, attrs: {}, dataset: {},
    setAttribute(k, v) { this.attrs[k] = v; if (k === "data-route-options") this.dataset.routeOptions = v; },
    getAttribute(k) { return k === "href" ? this.href : this.attrs[k]; },
    replaceChildren(...c) { this.children = c; },
    appendChild(c) { this.children.push(c); return c; },
    append(...c) { c.forEach((x) => this.children.push(x)); },
    addEventListener(t, fn) { (handlers[t] = handlers[t] || []).push(fn); },
    querySelector(sel) { return sel === ".konsol-home-tabs" ? box : null; } };
  return n;
}
const box = node("section");
const calls = [];
const created = [];
var frappe_ = {
  session: { user: "me@example.com" },
  xcall: (...a) => { calls.push(a); return Promise.resolve({}); },
  call: (...a) => { calls.push(a); return Promise.resolve({}); },
  route_options: null,
  set_route: (...a) => { events.push({ route: a, options: JSON.parse(JSON.stringify(frappe_.route_options)) }); frappe_.route_options = null; },
  widget: { WidgetGroup: function () {} },
  ui: { Tabs: function (opts) { created.push(opts); this.$el = node("tabs"); } },
};
global.frappe = frappe_;
global.document = { createElement: (t) => node(t), addEventListener() {} };
global.localStorage = { getItem() { return null; }, setItem() {} };
global.location = { href: "https://site.test/desk/konsol", origin: "https://site.test" };
global.requestAnimationFrame = () => 0;
global.$ = (x) => x;
global.root_element = node("root");
eval(fs.readFileSync(script, "utf8"));
const topts = created[0];
const atConstruct = calls.length;
const numbersPanel = topts.tabs[3].content();
const flat = (n) => [n].concat(...(n.children || []).map(flat));
const anchors = flat(numbersPanel).filter((n) => n.tag === "a");
const click = (a) => {
  let prevented = 0;
  const ev = { button: 0, preventDefault() { prevented++; }, target: { closest: () => a } };
  (handlers.click || []).forEach((h) => h(ev));
  return prevented;
};
const out = {
  at_construct: atConstruct, calls: calls.length,
  links: anchors.map((a) => ({ text: a.textContent, href: a.href, opts: a.dataset.routeOptions || null })),
  rows: numbersPanel.children.map((r) => flat(r).map((n) => n.textContent).filter(Boolean)),
  clicks: anchors.map((a) => { events.length = 0; const p = click(a); return { prevented: p, events: events.slice() }; }),
  calls_after_clicks: calls.length,
  html_sets: 0,
};
console.log(JSON.stringify(out));
"""

NUMBERS = ["Consolidated Statement", "Entity statement", "Statement Drill", "Consolidation Worksheet"]


def _run9():
    node = shutil.which("node")
    assert node
    result = subprocess.run([node, "-e", HARNESS_9, SCRIPT, "{}"], capture_output=True, text=True,
                            timeout=60)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_numbers_panel_lists_the_four_links_in_order():
    out = _run9()
    assert [l["text"] for l in out["links"]] == NUMBERS


def test_numbers_links_route_to_their_reports():
    out = _run9()
    want = [["query-report", "Consolidated Statement"], ["query-report", "Consolidated Statement"],
            ["query-report", "Statement Drill"], ["query-report", "Consolidation Worksheet"]]
    assert len(out["links"]) == 4
    for link, click, route in zip(out["links"], out["clicks"], want):
        assert click["prevented"] == 1, link["text"]
        assert [e["route"] for e in click["events"]] == [route], link["text"]


def test_entity_statement_sets_route_options_basis_entity_before_routing():
    out = _run9()
    entity = out["clicks"][1]["events"][0]
    assert entity["options"] == {"basis": "Entity"}
    # The other three route with no options set.
    for i in (0, 2, 3):
        assert out["clicks"][i]["events"][0]["options"] is None, out["links"][i]["text"]


def test_each_numbers_link_has_a_one_line_plain_description():
    out = _run9()
    assert len(out["rows"]) == 4
    for row, label in zip(out["rows"], NUMBERS):
        assert row[0] == label
        assert len(row) == 2 and row[1].strip() and "\n" not in row[1]


def test_numbers_panel_makes_no_server_call_on_activation_or_click():
    out = _run9()
    assert out["at_construct"] == 0
    assert out["calls"] == 0 and out["calls_after_clicks"] == 0

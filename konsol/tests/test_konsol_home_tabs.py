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
const [script] = process.argv.slice(1);
function node(tag) {
  return { tag, className: "", children: [], textContent: "",
    appendChild(c) { this.children.push(c); return c; },
    append(...c) { c.forEach((x) => this.children.push(x)); },
    querySelector(sel) { return sel === ".konsol-home-tabs" ? box : null; } };
}
const box = node("section");
const calls = [];
const created = [];
global.document = { createElement: (t) => node(t) };
global.root_element = node("root");
global.frappe = {
  xcall: (...a) => { calls.push(["xcall", a]); return Promise.resolve({}); },
  call: (...a) => { calls.push(["call", a]); return Promise.resolve({}); },
  ui: { Tabs: function (opts) { created.push(opts); this.opts = opts; this.$el = node("tabs"); } },
};
eval(fs.readFileSync(script, "utf8"));
const opts = created[0];
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
}));
"""


def _run():
    node = shutil.which("node")
    assert node, "node is required to run the Konsol Home Tabs script"
    assert os.path.exists(SCRIPT), "konsol_home_tabs.js (the home tabs block) is missing"
    result = subprocess.run([node, "-e", HARNESS, SCRIPT], capture_output=True,
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

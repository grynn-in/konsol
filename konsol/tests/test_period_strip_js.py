"""Period strips (Desk query reports): the Year and Period slider strips.
No browser: the script runs under node against a small fake DOM and a fake
`frappe.query_report`. What a real browser must still confirm (native arrow
keys firing `change`, layout, dark theme) is listed in the PR.
"""
import json
import os
import shutil
import subprocess

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(APP_DIR, "public", "js", "period_strip.js")

HARNESS = r"""
const script = process.argv[1];
const scenario = JSON.parse(process.argv[2]);

class El {
	constructor(tag) { this.tagName = tag; this.className = ""; this.children = []; this.parentNode = null;
		this.attrs = {}; this.listeners = {}; this.style = {}; this.textContent = ""; this.value = ""; this.disabled = false; }
	setAttribute(k, v) { this.attrs[k] = String(v); if (k === "class") this.className = v; }
	getAttribute(k) { return this.attrs[k]; }
	appendChild(c) { c.parentNode = this; this.children.push(c); return c; }
	insertBefore(c, ref) { c.parentNode = this; const i = this.children.indexOf(ref);
		this.children.splice(i < 0 ? this.children.length : i, 0, c); return c; }
	addEventListener(t, f) { (this.listeners[t] = this.listeners[t] || []).push(f); }
	fire(t) { (this.listeners[t] || []).forEach((f) => f({ type: t, target: this })); }
	getBoundingClientRect() { return { left: scenario.container_left || 0 }; }
	walk(out = []) { out.push(this); this.children.forEach((c) => c.walk(out)); return out; }
}
const doc = { createElement: (t) => new El(t), head: new El("head"), getElementById: () => null };
const body = new El("div");
const page_form = new El("form");
body.appendChild(page_form);

const vals = { ...scenario.filters };
const inputs = { fiscal_year: new El("input"), fiscal_period: new El("input") };
const calls = [];
const rects = { ...(scenario.rects || {}) };
const win = { listeners: {}, addEventListener(t, f) { (this.listeners[t] = this.listeners[t] || []).push(f); },
	fire(t) { (this.listeners[t] || []).forEach((f) => f({ type: t })); } };
global.window = win;
const report = {
	page: { page_form: [page_form] },
	get_filter_value: (k) => vals[k],
	get_filter: (k) => (k in inputs || rects[k]) ? {
		$input: inputs[k] && { 0: inputs[k], on: (t, f) => inputs[k].addEventListener(t, f) },
		$wrapper: rects[k] && { 0: { getBoundingClientRect: () => rects[k] } } } : undefined,
	set_filter_value: (o) => { calls.push(o); Object.assign(vals, o); },
};
global.document = doc;
const strip = require(script);
const info = { years: scenario.years, year_min: 1980, year_max: 2045, period_min: scenario.period_min ?? 0, period_max: scenario.period_max ?? 13 };
strip.attach(report, info);
if (scenario.attach_twice) strip.attach(report, info);

const all = body.walk();
const by = (cls) => all.filter((e) => e.className.split(" ").includes(cls));
const one = (cls) => by(cls)[0];
const state = () => ({
	year: { thumb: one("konsol-strip-year-range").value, label: one("konsol-strip-year-label").textContent },
	period: { thumb: one("konsol-strip-period-range").value, label: one("konsol-strip-period-label").textContent },
});
const disabled = () => ["year-prev", "year-next", "period-prev", "period-next"].map((c) => one(`konsol-strip-${c}`).disabled);
const act = {
	drag: (row, v) => { const r = one(`konsol-strip-${row}-range`); r.value = String(v); r.fire("input"); },
	release: (row, v) => { const r = one(`konsol-strip-${row}-range`); r.value = String(v); r.fire("change"); },
	click: (row, which) => one(`konsol-strip-${row}-${which}`).fire("click"),
	resize: (new_rects) => { Object.assign(rects, new_rects); win.fire("resize"); },
	type: (k, v) => { vals[k] = v; inputs[k].fire("change"); },
};
const log = [];
for (const [name, ...args] of scenario.steps || []) {
	act[name](...args);
	log.push({ calls: calls.length, last: calls[calls.length - 1], state: state(), disabled: disabled() });
}
const container = one("konsol-period-strips");
setTimeout(() => console.log(JSON.stringify({
	layout: { marginLeft: container.style.marginLeft, width: container.style.width },
	containers: by("konsol-period-strips").length,
	above_form: body.children.indexOf(one("konsol-period-strips")) < body.children.indexOf(page_form),
	ticks: by("konsol-strip-tick").map((e) => e.attrs["data-year"]),
	tick_left: by("konsol-strip-tick").map((e) => e.style.left),
	range_attrs: ["year", "period"].map((r) => {
		const e = one(`konsol-strip-${r}-range`);
		return [e.attrs.type, e.attrs.min, e.attrs.max, e.attrs.step];
	}),
	aria: Object.fromEntries(["year-prev", "year-next", "period-prev", "period-next", "year-range", "period-range"]
		.map((c) => [c, one(`konsol-strip-${c}`).attrs["aria-label"]])),
	label_titles: ["year", "period"].map((r) => one(`konsol-strip-${r}-label`).attrs.title || ""),
	captions: ['open', 'close'].map((c) => by('konsol-strip-cap-' + c).map((e) => e.textContent)[0]),
	initial: state(), initial_disabled: disabled(), log,
})), 250);
"""


def _run(scenario):
    node = shutil.which("node")
    assert node, "node is required"
    assert os.path.exists(SCRIPT), "period_strip.js is missing"
    r = subprocess.run([node, "-e", HARNESS, SCRIPT, json.dumps(scenario)],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


BASE = {"filters": {"fiscal_year": 2024, "fiscal_period": 6}, "years": [2023, 2024]}


def test_strip_renders_above_the_filter_row_with_both_rows():
    out = _run(BASE)
    assert out["containers"] == 1 and out["above_form"]
    assert out["range_attrs"] == [["range", "1980", "2045", "1"], ["range", "0", "13", "1"]]
    assert out["initial"] == {"year": {"thumb": "2024", "label": "2024"},
                              "period": {"thumb": "6", "label": "6"}}


def test_release_sets_both_filters_in_one_call():
    out = _run({**BASE, "steps": [["release", "period", 9]]})
    assert out["log"][0]["calls"] == 1
    assert out["log"][0]["last"] == {"fiscal_year": 2024, "fiscal_period": 9}
    out = _run({**BASE, "steps": [["release", "year", 2020]]})
    assert out["log"][0]["last"] == {"fiscal_year": 2020, "fiscal_period": 6}


def test_dragging_updates_the_label_but_changes_no_filter():
    out = _run({**BASE, "steps": [["drag", "period", 3], ["drag", "period", 4]]})
    assert [s["calls"] for s in out["log"]] == [0, 0]
    assert out["log"][1]["state"]["period"]["label"] == "4"


def test_buttons_step_by_one_and_clamp_at_the_ends():
    out = _run({"filters": {"fiscal_year": 2045, "fiscal_period": 13}, "years": [],
                "steps": [["click", "period", "next"], ["click", "year", "next"],
                          ["click", "period", "prev"], ["click", "year", "prev"]]})
    lasts = [s.get("last") for s in out["log"]]
    # a step that changes nothing sets no filter (no refresh)
    assert lasts[2:] == [{"fiscal_year": 2045, "fiscal_period": 12},
                         {"fiscal_year": 2044, "fiscal_period": 12}]
    assert [s["calls"] for s in out["log"]] == [0, 0, 1, 2]


def test_buttons_are_disabled_at_and_beyond_the_ends():
    # order: year-prev, year-next, period-prev, period-next
    def disabled(year, period):
        return _run({"filters": {"fiscal_year": year, "fiscal_period": period},
                     "years": []})["initial_disabled"]
    assert disabled(2024, 6) == [False, False, False, False]
    assert disabled(1980, 0) == [True, False, True, False]
    assert disabled(2045, 13) == [False, True, False, True]
    assert disabled(1975, 14) == [True, False, False, True]
    assert disabled(2050, -1) == [False, True, True, False]


def test_typing_a_value_updates_the_disabled_buttons():
    out = _run({**BASE, "steps": [["type", "fiscal_period", 13], ["type", "fiscal_period", 5]]})
    assert [s["disabled"][2:] for s in out["log"]] == [[False, True], [False, False]]


def test_stepping_from_an_out_of_range_value_does_not_move_it_backwards():
    out = _run({"filters": {"fiscal_year": 2024, "fiscal_period": 14}, "years": [],
                "steps": [["click", "period", "next"], ["click", "period", "prev"]]})
    assert [s["calls"] for s in out["log"]] == [0, 1]
    assert out["log"][1]["last"] == {"fiscal_year": 2024, "fiscal_period": 13}


def test_out_of_range_typed_value_puts_thumb_at_end_and_label_shows_real_value():
    out = _run({"filters": {"fiscal_year": 1975, "fiscal_period": 14}, "years": []})
    assert out["initial"] == {"year": {"thumb": "1980", "label": "1975"},
                              "period": {"thumb": "13", "label": "14"}}
    out = _run({"filters": {"fiscal_year": 2024, "fiscal_period": 6}, "years": [],
                "steps": [["type", "fiscal_period", -1]]})
    assert out["log"][0]["state"]["period"] == {"thumb": "0", "label": "-1"}
    assert out["log"][0]["calls"] == 0  # following the typed box never sets a filter


def test_typed_change_repositions_the_thumb():
    out = _run({**BASE, "steps": [["type", "fiscal_year", 2031], ["type", "fiscal_period", 2]]})
    assert out["log"][1]["state"] == {"year": {"thumb": "2031", "label": "2031"},
                                      "period": {"thumb": "2", "label": "2"}}


def test_attach_twice_renders_one_container():
    assert _run({**BASE, "attach_twice": True})["containers"] == 1


def test_year_ticks_only_for_declared_years_in_range():
    out = _run({"filters": {"fiscal_year": 2024, "fiscal_period": 6},
                "years": [2025, 2023, 1970, 2050, 2023]})
    assert out["ticks"] == ["2023", "2025"]
    assert out["tick_left"] == [f"{(y - 1980) / 65 * 100}%" for y in (2023, 2025)]


def test_buttons_and_ranges_have_aria_labels():
    out = _run(BASE)
    assert out["aria"] == {"year-prev": "Previous year", "year-next": "Next year",
                           "period-prev": "Previous period", "period-next": "Next period",
                           "year-range": "Fiscal year", "period-range": "Fiscal period"}


def test_out_of_range_label_explains_itself_and_is_not_red():
    out = _run({"filters": {"fiscal_year": 1975, "fiscal_period": 6}, "years": []})
    assert "Outside the strip" in out["label_titles"][0] and "type it in the box" in out["label_titles"][0]
    assert out["label_titles"][1] == ""
    out = _run({**BASE, "steps": [["type", "fiscal_year", 1970], ["type", "fiscal_year", 2000]]})
    assert out["log"][1]["state"]["year"]["label"] == "2000"
    css = open(SCRIPT).read()
    rule = [line for line in css.splitlines() if "out-of-range" in line and "{" in line][0]
    assert "red" not in rule


FY = {"left": 120, "right": 300}
GROUP = {"left": 520, "right": 700}
PERIOD = {"left": 320, "right": 440}


def test_strips_span_fiscal_year_left_to_consolidation_group_right():
    out = _run({**BASE, "rects": {"fiscal_year": FY, "fiscal_period": PERIOD, "consolidation_group": GROUP}})
    assert out["layout"] == {"marginLeft": "120px", "width": "580px"}


def test_the_container_own_left_is_subtracted():
    out = _run({**BASE, "container_left": 20,
                "rects": {"fiscal_year": FY, "fiscal_period": PERIOD, "consolidation_group": GROUP}})
    assert out["layout"] == {"marginLeft": "100px", "width": "580px"}


def test_without_a_group_filter_the_span_ends_at_fiscal_period():
    out = _run({**BASE, "rects": {"fiscal_year": FY, "fiscal_period": PERIOD}})
    assert out["layout"] == {"marginLeft": "120px", "width": "320px"}


def test_resize_remeasures():
    out = _run({**BASE, "rects": {"fiscal_year": FY, "fiscal_period": PERIOD, "consolidation_group": GROUP},
                "steps": [["resize", {"fiscal_year": {"left": 80, "right": 260},
                                      "consolidation_group": {"left": 400, "right": 500}}]]})
    assert out["layout"] == {"marginLeft": "80px", "width": "420px"}


def test_unmeasurable_filters_fall_back_to_the_plain_layout():
    out = _run(BASE)  # no rects: fake filters have no wrapper
    assert out["layout"] == {"marginLeft": "", "width": ""}


def test_spacing_between_the_strips_and_before_the_filter_row():
    css = open(SCRIPT).read()
    assert "margin:0 0 10px" in css and "margin-bottom:8px" in css


def test_period_zero_and_thirteen_are_in_range_and_read_open_and_close():
    out = _run({"filters": {"fiscal_year": 2024, "fiscal_period": 0}, "years": [],
                "steps": [["type", "fiscal_period", 13], ["drag", "period", 0], ["drag", "period", 13],
                          ["drag", "period", 7]]})
    first = _run({"filters": {"fiscal_year": 2024, "fiscal_period": 0}, "years": []})
    assert first["initial"]["period"] == {"thumb": "0", "label": "Open"}
    labels = [s["state"]["period"]["label"] for s in out["log"]]
    assert labels == ["Close", "Open", "Close", "7"]
    assert out["log"][0]["state"]["period"]["thumb"] == "13"
    assert out["label_titles"][1] == ""  # not out of range


def test_the_year_label_is_always_the_number():
    out = _run({"filters": {"fiscal_year": 2024, "fiscal_period": 6}, "years": []})
    assert out["initial"]["year"]["label"] == "2024"


def test_open_and_close_captions_sit_at_the_ends_of_the_period_track():
    out = _run(BASE)
    assert out["captions"] == ["Open", "Close"]

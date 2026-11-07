"""Konsol home: look and feel (Deepak Pai, 7 Nov 2026: "improve the look and
feel of the home page"). Presentation only; no server data contract changes.

Measured on the live page before this change (1456 px window, content column
878 px, light theme, Frappe 16.55.2): each of the six close-band cards was
79 px tall and showed only a 20 px indicator pill; the checklist block was 14
single-column lines, ~600 px tall; seven charts came before the two action
sections; the eight waiting shortcuts truncated ("Business combinati...");
bar x-axis labels rendered "FY2025 ..." because frappe-charts budgets 60 % of
a label's slot (getShortenedLabels, SERIES_LABEL_SPACE_RATIO) and cuts the
rest with " ...".

What this pins, file by file:

- ``hooks.py`` hooks ``konsol_desk.css`` and the file exists.
- Every selector in ``konsol_desk.css`` is scoped under the Konsol workspace
  body, so no other Desk page changes; the file names no hex colour (dark
  theme and the per-site Brand Colour both come through Frappe variables).
- The workspace reads band -> checklist -> actions -> trends: every chart
  block comes after the last shortcut block; the waiting shortcuts carry a
  short noun label whose "waiting" lives in the section header and the
  ``{} waiting`` count badge; every content shortcut has its row and back.
- The checklist block draws a progress bar and lays out two columns on a
  wide block, from a container query on the block's own width.
- Period-keyed charts declare their x-axis a series with a wider label
  budget; categorical charts are left to frappe-charts' default.
"""
import ast
import json
import os
import re

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODULE_DIR = os.path.join(APP_DIR, "consolidation")
CSS = os.path.join(APP_DIR, "public", "css", "konsol_desk.css")
CSS_URL = "/assets/konsol/css/konsol_desk.css"
SCOPE = 'body[data-route="Workspaces/Konsol"]'
WORKSPACE = os.path.join(MODULE_DIR, "workspace", "konsol", "konsol.json")
BLOCK_DIR = os.path.join(MODULE_DIR, "custom_html_block", "konsol_close_checklist")
SINCE = "2026-11-07 22:30:00"

#: Charts whose x labels are periods (period_name), in reading order.
PERIOD_CHARTS = ("TB Submission Progress", "Net Sales and Net Result",
                 "Closing vs Average Rate", "Days to Close")
#: Charts whose x labels are categories: no series option.
CATEGORY_CHARTS = ("Approvals Backlog", "Top IC Differences")


def _read(path):
    with open(path) as f:
        return f.read()


def _hooks():
    tree = ast.parse(_read(os.path.join(APP_DIR, "hooks.py")))
    return {n.targets[0].id: ast.literal_eval(n.value) for n in tree.body
            if isinstance(n, ast.Assign) and isinstance(n.targets[0], ast.Name)
            and isinstance(n.value, (ast.List, ast.Constant, ast.Dict, ast.Tuple))}


def _ws():
    return json.loads(_read(WORKSPACE))


def _chart(name):
    slug = name.lower().replace(" ", "_")
    return json.loads(_read(os.path.join(MODULE_DIR, "dashboard_chart", slug, slug + ".json")))


def _rules(css):
    """(selector, declarations) per rule, comments stripped; at-rules refused."""
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    rules = []
    for chunk in css.split("}"):
        if not chunk.strip():
            continue
        selector, _, body = chunk.partition("{")
        assert not selector.strip().startswith("@"), selector.strip()
        rules.append((selector.strip(), body))
    return rules


# --- A. page CSS -----------------------------------------------------------------


def test_the_desk_css_is_hooked_and_exists():
    hooks = _hooks()
    assert hooks["app_include_css"] == [CSS_URL]
    assert os.path.isfile(CSS)
    assert hooks["app_include_js"] == ["/assets/konsol/js/konsol_brand.js"]  # untouched


def test_every_desk_css_rule_is_scoped_to_the_konsol_workspace():
    rules = _rules(_read(CSS))
    assert rules
    for selector, _ in rules:
        for part in selector.split(","):
            assert part.strip().startswith(SCOPE), part.strip()


def _no_comments(css):
    return re.sub(r"/\*.*?\*/", "", css, flags=re.S)


def test_the_desk_css_names_no_colour_of_its_own():
    css = _no_comments(_read(CSS))  # a comment may cite konsol#407
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b", css)
    assert not re.search(r"\b(rgb|hsl)a?\(", css)
    for var in ("--heading-color", "--primary"):
        assert "var(%s)" % var in css, var


def test_the_desk_css_turns_the_band_pills_into_stat_values():
    rules = dict(_rules(_read(CSS)))
    pill = next(body for sel, body in rules.items()
                if ".number-widget-box" in sel and ".indicator-pill" in sel and "::before" not in sel)
    assert "font-size: var(--text-xl)" in pill
    assert "font-weight: 600" in pill
    assert "color: var(--heading-color)" in pill
    assert "padding: 0" in pill and "background: none" in pill
    assert "white-space: normal" in pill
    card = next(body for sel, body in rules.items() if sel.endswith(".number-widget-box"))
    assert "min-height:" in card


# --- C. workspace layout -------------------------------------------------------


def test_the_home_page_reads_band_checklist_actions_then_trends():
    content = json.loads(_ws()["content"])
    kinds = [b["type"] for b in content]
    first = {k: kinds.index(k) for k in ("number_card", "custom_block", "shortcut", "chart")}
    last_shortcut = len(kinds) - 1 - kinds[::-1].index("shortcut")
    assert first["number_card"] < first["custom_block"] < first["shortcut"], first
    assert first["chart"] > last_shortcut, (first["chart"], last_shortcut)
    headers = [re.sub(r"<[^>]+>", "", b["data"]["text"]) for b in content if b["type"] == "header"]
    assert headers == ["Where the close stands", "Waiting for approval", "Monthly close",
                       "Close trends"], headers
    ids = [b["id"] for b in content]
    assert len(ids) == len(set(ids))


def test_waiting_shortcuts_carry_a_short_noun_label():
    ws = _ws()
    waiting = [s for s in ws["shortcuts"] if s.get("stats_filter")]
    assert len(waiting) == 8
    for s in waiting:
        assert len(s["label"]) <= 22, s["label"]
        assert "waiting" not in s["label"].lower(), s["label"]
        assert s["format"] == "{} waiting", s["label"]
        assert s["color"] == "Orange", s["label"]
    assert [s["label"] for s in waiting] == [
        "Journals", "Business combinations", "Business disposals", "Exchange rates",
        "Ownership periods", "Equity rates", "IC balances", "Builds"]
    assert ws["modified"] >= SINCE, ws["modified"]


def test_every_content_shortcut_has_its_row_and_every_row_is_on_the_page():
    ws = _ws()
    rows = {s["label"] for s in ws["shortcuts"]}
    on_page = [b["data"]["shortcut_name"] for b in json.loads(ws["content"])
               if b["type"] == "shortcut"]
    assert set(on_page) == rows, sorted(set(on_page) ^ rows)
    assert len(on_page) == len(set(on_page))


# --- B. checklist block --------------------------------------------------------


def test_the_checklist_draws_a_progress_bar_from_done_over_total():
    js = _read(os.path.join(BLOCK_DIR, "konsol_close_checklist.js"))
    css = _read(os.path.join(BLOCK_DIR, "konsol_close_checklist.css"))
    assert "kc-progress" in js and "kc-progress-fill" in js
    assert '"progressbar"' in js
    assert "data.done / data.total" in js
    assert ".kc-progress-fill" in css and "var(--primary)" in css
    # server strings stay text, and a line links only to a /desk/ route
    assert "innerHTML" not in js and "textContent" in js
    assert 'startsWith("/desk/")' in js


def test_the_checklist_lays_out_two_columns_on_a_wide_block():
    css = _read(os.path.join(BLOCK_DIR, "konsol_close_checklist.css"))
    assert "container-type: inline-size" in css
    assert "@container (min-width: 720px)" in css
    assert "columns: 2" in css
    assert "break-inside: avoid" in css
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b", _no_comments(css))


def test_the_checklist_glyph_is_a_small_round_indicator():
    css = _read(os.path.join(BLOCK_DIR, "konsol_close_checklist.css"))
    js = _read(os.path.join(BLOCK_DIR, "konsol_close_checklist.js"))
    assert "border-radius: 50%" in css
    assert "indicator-pill ${TONE[line.state]} kc-glyph" in js  # tone classes kept
    assert 'ok: "✓"' in js  # GLYPH text kept


# --- D. chart labels -----------------------------------------------------------


def test_period_keyed_charts_declare_a_series_axis_with_a_wider_label_budget():
    for name in PERIOD_CHARTS:
        doc = _chart(name)
        options = json.loads(doc["custom_options"])
        axis = options["axisOptions"]
        assert axis["xIsSeries"] == 1, name
        assert 0.6 < axis["seriesLabelSpaceRatio"] <= 1, name
        assert doc["timeseries"] == 0, name  # no timespan filter row on the widget
        assert doc["modified"] >= SINCE, name
    assert json.loads(_chart("TB Submission Progress")["custom_options"])["barOptions"] == {"stacked": 1}


def test_categorical_charts_keep_the_default_axis():
    for name in CATEGORY_CHARTS:
        options = json.loads(_chart(name).get("custom_options") or "{}")
        assert "axisOptions" not in options, name

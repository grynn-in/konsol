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

- ``hooks.py`` hooks ``konsol_desk.bundle.css`` and the file exists.
- Every selector in ``konsol_desk.bundle.css`` is scoped under the Konsol workspace
  body, so no other Desk page changes; the file names no hex colour (dark
  theme and the per-site Brand Colour both come through Frappe variables).
- The workspace reads band -> checklist -> actions -> trends: every chart
  block comes after the last shortcut block; the waiting shortcuts carry a
  short noun label whose "waiting" lives in the section header and the
  ``{} waiting`` count badge; every content shortcut has its row and back.
- The checklist block draws a progress bar and lays out two columns on a
  wide block, from a container query on the block's own width.
- Period-keyed charts declare their x-axis a series with a wider label
  budget: whole labels where they fit, alternate whole labels where they do
  not, never stubs; categorical charts are left to frappe-charts' default.
"""
import json
import os
import re

from konsol.tests.test_brand_colour import _hooks  # one parser of hooks.py

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODULE_DIR = os.path.join(APP_DIR, "consolidation")
CSS = os.path.join(APP_DIR, "public", "css", "konsol_desk.bundle.css")
CSS_URL = "konsol_desk.bundle.css"
SCOPE = 'body[data-route="Workspaces/Konsol"]'
WORKSPACE = os.path.join(MODULE_DIR, "workspace", "konsol", "konsol.json")
BLOCK_DIR = os.path.join(MODULE_DIR, "custom_html_block", "konsol_close_checklist")
SINCE = "2026-11-07 22:30:00"

#: Charts whose x labels are periods (period_name, 10 chars). The wider
#: budget shows them whole where the slot allows (7 labels at col 12); where
#: it does not (col 6, or 12 periods late in the year) xIsSeries drops
#: alternate labels and shows the rest whole, instead of "FY2025 ..." stubs.
PERIOD_CHARTS = ("TB Submission Progress", "Net Sales and Net Result", "Closing vs Average Rate")
#: Charts left on frappe-charts' default axis: categorical labels (Approvals
#: Backlog, Top IC Differences), and Days to Close, whose 27-char labels
#: ("FY2025 P01 (not signed off)") fit no half-width card either way: the
#: series branch would blank five and clip the first and last (review of
#: #439); the shorter label is server text, not this PR.
DEFAULT_AXIS_CHARTS = ("Approvals Backlog", "Top IC Differences", "Days to Close")


def _read(path):
    with open(path) as f:
        return f.read()


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
        assert not selector.strip().startswith("@"), (
            "konsol_desk.bundle.css is flat by design: an at-rule would need this parser to read "
            "its inner selectors; got %r" % selector.strip())
        rules.append((selector.strip(), body))
    return rules


# --- A. page CSS -----------------------------------------------------------------


def test_the_desk_css_is_hooked_and_exists():
    hooks = _hooks()
    assert CSS_URL in hooks["app_include_css"]
    assert os.path.isfile(CSS)


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
    assert "var(--heading-color)" in css
    # the accent is the Brand Colour when the site has one; --primary always
    # exists (Bootstrap #171717, invisible on the dark theme), --konsol-brand
    # only when konsol_brand.js found a palette, so the fallback is declared
    assert "var(--konsol-brand, var(--heading-color))" in css
    assert "var(--primary)" not in css


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


def test_the_section_header_margin_skips_the_first_block():
    rules = dict(_rules(_read(CSS)))
    (sel,) = [sel for sel, body in rules.items() if sel.endswith(".ce-header") and "margin-top" in body]
    assert ":not(:first-child)" in sel, sel


# --- C. workspace layout -------------------------------------------------------


TABS_BLOCK = "Konsol Home Tabs"
TABS_JS = os.path.join(MODULE_DIR, "custom_html_block", "konsol_home_tabs", "konsol_home_tabs.js")


def test_the_home_page_is_the_one_tabs_block():
    """#497 row 11: the page body is a single full-width custom block. The old
    band -> checklist -> actions -> trends order and the four section headers
    now live in the block (tabs Close, Approvals, Trends, Numbers)."""
    ws = _ws()
    content = json.loads(ws["content"])
    assert content == [{"id": "kht", "type": "custom_block",
                        "data": {"custom_block_name": TABS_BLOCK, "col": 12}}], content
    assert TABS_BLOCK in [b["custom_block_name"] for b in ws["custom_blocks"]]
    js = _read(TABS_JS)
    labels = re.findall(r'\{ label: "(\w+)", content: panel\(', js)
    assert labels == ["Close", "Approvals", "Trends", "Numbers"], labels


def test_waiting_shortcuts_carry_a_short_noun_label():
    """The waiting shortcuts use the doctype's one label (konsol/desk_labels.py);
    the longest is 23 characters, which still fits a col-4 card. Deepak Pai,
    7 Nov 2026 (Desk IA, #443): one label everywhere beats #439's short nouns."""
    ws = _ws()
    waiting = [s for s in ws["shortcuts"] if s.get("stats_filter")]
    assert len(waiting) == 8
    for s in waiting:
        assert len(s["label"]) <= 23, s["label"]
        assert "waiting" not in s["label"].lower(), s["label"]
        assert s["format"] == "{} waiting", s["label"]
        assert s["color"] == "Orange", s["label"]
    assert [s["label"] for s in waiting] == [
        "Consolidation Journals", "Business Combinations", "Business Disposals",
        "Group Exchange Rates", "Ownership Periods", "Historical Equity Rates",
        "IC Balances", "Build Approvals"]


def test_no_two_shortcut_labels_differ_only_by_case():
    """Frappe finds a content block's row by label (workspace/blocks/block.js
    find(obj.label == __(block_name))); "Exchange rates" next to the
    Monthly-close "Exchange Rates" would be told apart by case alone, and
    not at all once translated (review of #439)."""
    ws = _ws()
    labels = [s["label"] for s in ws["shortcuts"]]
    folded = [l.casefold() for l in labels]
    assert len(set(folded)) == len(labels), sorted(l for l in labels if folded.count(l.casefold()) > 1)
    assert ws["modified"] >= SINCE, ws["modified"]


def test_every_shortcut_the_tabs_block_names_has_its_row():
    """The block reads the shortcut child table through get_desktop_page, so a
    target in its WAITING or MONTHLY_CLOSE list with no row would vanish
    silently; the content no longer carries shortcut blocks. The block names
    each shortcut by [type, link_to], not label (#497 review R4)."""
    ws = _ws()
    rows = [(s["type"], s["link_to"]) for s in ws["shortcuts"]]
    js = _read(TABS_JS)
    named = []
    for const in ("MONTHLY_CLOSE", "WAITING"):
        body = re.search(r"const %s = \[(.*?)\n\];" % const, js, re.S).group(1)
        named += re.findall(r'\["([^"]+)", "([^"]+)"\]', body)
    assert sorted(named) == sorted(rows), sorted(set(named) ^ set(rows))
    assert len(named) == len(set(named))
    assert not [b for b in json.loads(ws["content"]) if b["type"] == "shortcut"]


# --- B. checklist block --------------------------------------------------------


def test_the_checklist_draws_a_progress_bar_from_done_over_total():
    js = _read(os.path.join(BLOCK_DIR, "konsol_close_checklist.js"))
    css = _read(os.path.join(BLOCK_DIR, "konsol_close_checklist.css"))
    assert "kc-progress" in js and "kc-progress-fill" in js
    assert '"progressbar"' in js
    # one clamped value drives both the bar width and aria-valuenow
    assert "const done = Math.min(data.total, Math.max(0, data.done))" in js
    assert "${done} of ${data.total} done" in js  # the text and the bar agree
    assert "(100 * done) / data.total" in js
    assert 'setAttribute("aria-valuenow", String(done))' in js
    assert 'setAttribute("aria-labelledby", count.id)' in js
    assert ".kc-progress-fill" in css and "var(--konsol-brand, var(--heading-color))" in css
    assert "var(--primary)" not in css
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


def test_categorical_and_long_label_charts_keep_the_default_axis():
    for name in DEFAULT_AXIS_CHARTS:
        options = json.loads(_chart(name).get("custom_options") or "{}")
        assert "axisOptions" not in options, name

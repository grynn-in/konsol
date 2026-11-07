"""Konsol sidebars: section headings (Deepak Pai, 7 Nov 2026: "make sure things
like Trial Balances (sections?) get a slightly darker background and are spaced
closer together"; then "Only vertical spacing needs to be tighter..the
horizontal is fine").

What this pins:

- ``hooks.py`` loads ``konsol_sidebar.bundle.css`` and the file exists.
- Every rule is scoped to konsol's own sidebars. Frappe v16 writes the shell's
  name onto ``.body-sidebar`` as ``data-title`` (frappe/public/js/frappe/ui/
  sidebar/sidebar.js ``setup``: ``this.$sidebar.attr("data-title",
  this.current_module)``), so the scope is exactly the shipped konsol sidebar
  titles. A sidebar added or renamed without updating the CSS fails here
  instead of silently losing the style.
- Colours are Frappe variables only (dark theme and Brand Colour come
  through); no hex.
- Only vertical spacing changes: no rule sets a horizontal margin or padding,
  ``left``/``right``, ``width`` or ``gap``.
"""
import glob
import json
import os
import re

from konsol.tests.test_brand_colour import _hooks  # one parser of hooks.py

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CSS = os.path.join(APP_DIR, "public", "css", "konsol_sidebar.bundle.css")
CSS_URL = "konsol_sidebar.bundle.css"

HORIZONTAL = re.compile(
    r"^\s*(margin|padding)-(left|right|inline\S*)\s*:|^\s*(left|right|width|gap|column-gap)\s*:"
    r"|^\s*(margin|padding)\s*:\s*\S+\s+\S+",
    re.M,
)


def _read(path):
    with open(path) as f:
        return f.read()


def _shipped_sidebar_titles():
    titles = set()
    for path in glob.glob(os.path.join(APP_DIR, "*", "sidebar", "*", "*.json")):
        d = json.loads(_read(path))
        if d.get("doctype") == "Sidebar":
            titles.add(d["title"])
    return titles


def _rules(css):
    """(selector list, body) for each rule, comments stripped."""
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    return [(sel.strip(), body) for sel, body in re.findall(r"([^{}]+)\{([^{}]*)\}", css)]


def test_hooks_load_the_sidebar_css():
    hooks = _hooks()
    assert CSS_URL in hooks["app_include_css"], hooks["app_include_css"]
    assert os.path.exists(CSS)


def test_every_rule_is_scoped_to_exactly_the_konsol_sidebars():
    titles = _shipped_sidebar_titles()
    assert titles, "no shipped sidebars found"
    rules = _rules(_read(CSS))
    assert rules, "no rules"
    for selector, _ in rules:
        scoped = set(re.findall(r'\.body-sidebar\[data-title="([^"]+)"\]', selector))
        assert scoped == titles, (selector, sorted(scoped), sorted(titles))
        # Every comma-separated selector starts at a konsol sidebar.
        for part in re.split(r",(?![^(]*\))", selector):
            assert part.strip().startswith(":is(.body-sidebar[data-title="), part


def test_headings_are_a_brand_green_band_with_white_text():
    """Second round (Deepak Pai, 7 Nov 2026): "more tightly spaced and darker
    green background than what you did". The band is the site's Brand Colour
    (konsol_brand.js --konsol-brand; Frappe's --primary when no Brand Colour is
    set). brand_model.validate refuses a Brand Colour white text cannot be read
    on (WCAG AA 4.5:1), so white label and chevron are safe on any accepted one."""
    rules = _rules(_read(CSS))
    heading = [b for s, b in rules if s.rstrip().endswith(".section-break")]
    assert heading, "no heading rule"
    body = heading[0]
    # Third round (Deepak Pai, 7 Nov 2026): "a slightly darker colored
    # sections" -> the darker brand shade konsol/brand.py derives (strong).
    assert "background-color: var(--konsol-brand-strong, var(--primary))" in body, body
    assert "color: var(--white) !important" in body, body
    icon = [b for s, b in rules if ".section-break" in s and "stroke" in b]
    assert icon and "var(--white)" in icon[0], "chevron must be white on the band"


def test_headings_sit_tightly():
    """Frappe: 16px above a heading and a 28px row (sidebar.scss). Round one
    took the gap to 8px; round two asked for tighter: 2px and a 24px row."""
    rules = _rules(_read(CSS))
    gaps = [b for s, b in rules if ".section-item" in s and "margin-top" in b]
    assert gaps, "no gap rule above a heading"
    assert int(re.search(r"margin-top:\s*(\d+)px", gaps[0]).group(1)) <= 2, gaps[0]
    heights = [b for s, b in rules if s.rstrip().endswith(".section-break") and "height" in b]
    assert heights and int(re.search(r"height:\s*(\d+)px", heights[0]).group(1)) <= 24, heights


def test_every_section_starts_collapsed():
    """Deepak Pai, 7 Nov 2026: "also all collapsed". keep_closed closes a
    section on render (ui/sidebar/sidebar_item.js enable_collapsible); a user's
    own open/close, kept in localStorage, wins after that."""
    for path in glob.glob(os.path.join(APP_DIR, "*", "sidebar", "*", "*.json")):
        d = json.loads(_read(path))
        for item in d.get("items", []):
            if item["type"] == "Section Break":
                assert item.get("collapsible") == 1 and item.get("keep_closed") == 1, (
                    os.path.relpath(path, APP_DIR), item["label"])


def test_only_vertical_spacing_and_no_hex_colours():
    css = re.sub(r"/\*.*?\*/", "", _read(CSS), flags=re.S)
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b", css)
    assert not HORIZONTAL.search(css), HORIZONTAL.search(css).group(0)


def test_heading_corners_are_nearly_square():
    """Deepak Pai, 7 Nov 2026: "reduce rounded edges...looks too appy...like it
    less rounded". The band was Frappe's --radius (8px); now 2px."""
    rules = _rules(_read(CSS))
    heading = [b for s, b in rules if s.rstrip().endswith(".section-break")]
    radius = re.search(r"border-radius:\s*([^;]+);", heading[0]).group(1).strip()
    assert radius == "2px", radius

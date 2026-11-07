"""Konsol sidebars: section headings (Deepak Pai, 7 Nov 2026: "make sure things
like Trial Balances (sections?) get a slightly darker background and are spaced
closer together"; then "Only vertical spacing needs to be tighter..the
horizontal is fine").

What this pins:

- ``hooks.py`` loads ``konsol_sidebar.css`` and the file exists.
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
CSS = os.path.join(APP_DIR, "public", "css", "konsol_sidebar.css")
CSS_URL = "/assets/konsol/css/konsol_sidebar.css"

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


def test_headings_get_a_darker_background_and_tighter_vertical_gaps():
    rules = _rules(_read(CSS))
    bodies = {sel: body for sel, body in rules}
    heading = [b for s, b in bodies.items() if s.rstrip().endswith(".section-break")]
    assert heading and "background-color: var(--surface-gray-2)" in heading[0], heading
    gaps = [b for s, b in bodies.items() if ".section-item" in s and "margin-top" in b]
    assert gaps, "no tighter gap above a heading"
    px = int(re.search(r"margin-top:\s*(\d+)px", gaps[0]).group(1))
    assert px < 16, px  # Frappe's own gap above a heading is 16px (sidebar.scss)


def test_only_vertical_spacing_and_no_hex_colours():
    css = re.sub(r"/\*.*?\*/", "", _read(CSS), flags=re.S)
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b", css)
    assert not HORIZONTAL.search(css), HORIZONTAL.search(css).group(0)

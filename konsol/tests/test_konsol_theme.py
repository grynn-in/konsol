"""Konsol Desk theme: nearly square corners everywhere (Deepak Pai, 7 Nov 2026:
"less rounded boxes everywhere", then "Whole Desk" when asked how far; the
alternative, konsol pages only, was rejected).

Frappe v16 defines every radius as a variable on :root
(frappe/public/css/espresso/radius.css: --radius 8px, --radius-md 10px,
--radius-lg 12px, ...). konsol_theme.bundle.css, loaded by app_include_css after
desk.bundle.css, sets each box radius to 2px. --radius-full (9999px) and
--radius-9 (999px) stay, so avatars and status pills keep their round shape.
"""
import os
import re

from konsol.tests.test_brand_colour import _hooks

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CSS = os.path.join(APP_DIR, "public", "css", "konsol_theme.bundle.css")
URL = "konsol_theme.bundle.css"

#: Frappe's box radii (radius.css). Every one is set to 2px.
BOX = ("--radius", "--radius-sm", "--radius-md", "--radius-lg", "--radius-xl", "--radius-2xl",
       "--radius-1", "--radius-2", "--radius-3", "--radius-4", "--radius-5", "--radius-6",
       "--radius-7", "--radius-8")
#: Round on purpose: avatars, pills, toggles.
ROUND = ("--radius-full", "--radius-9")


def _css():
    with open(CSS) as f:
        return re.sub(r"/\*.*?\*/", "", f.read(), flags=re.S)


def test_hooks_load_the_theme_after_frappe():
    css = _hooks()["app_include_css"]
    assert URL in css, css


def test_every_box_radius_is_two_pixels_and_round_things_stay_round():
    css = _css()
    rules = re.findall(r"([^{}]+)\{([^{}]*)\}", css)
    assert [sel.strip() for sel, _ in rules] == ["html:root"], [s for s, _ in rules]
    decls = dict(re.findall(r"(--[a-z0-9-]+)\s*:\s*([^;]+);", rules[0][1]))
    for var in BOX:
        assert decls.get(var, "").strip() == "2px", (var, decls.get(var))
    for var in ROUND:
        assert var not in decls, var
    assert set(decls) == set(BOX), sorted(set(decls) - set(BOX))

"""The K tile matches the sidebar bands (Deepak Pai, 7 Nov 2026: "Make the K
icon also same dark green or greener?").

The logo is a static SVG (hooks.py add_to_apps_screen), so it cannot follow a
CSS variable. It takes the darker shade of the declared default Brand Colour,
brand_model.palette(DEFAULT)["strong"], which the bands use on a default site,
with a white K (contrast over 4.5:1, brand_model.MIN_CONTRAST). Its corners
follow "less rounded boxes everywhere": rx 4 on the 80px tile, the same
proportion as 2px on a 40px rail tile.
"""
import importlib.util
import os
import re

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOGO = os.path.join(APP_DIR, "public", "images", "konsol-logo.svg")


def _brand():
    spec = importlib.util.spec_from_file_location("_brand_model_logo", os.path.join(APP_DIR, "brand_model.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _svg():
    with open(LOGO) as f:
        return f.read()


def test_the_tile_is_the_default_brands_dark_shade_with_a_white_k():
    b = _brand()
    strong = b.palette(b.DEFAULT)["strong"]
    rect = re.search(r"<rect\b[^>]*>", _svg()).group(0)
    fill = re.search(r'fill="(#[0-9A-Fa-f]{6})"', rect).group(1)
    assert fill.upper() == strong.upper(), (rect, strong)
    assert 'fill="#FFFFFF"' in re.search(r"<text\b[^>]*>", _svg()).group(0)
    assert b.contrast_on_white(strong) >= b.MIN_CONTRAST


def test_the_tile_corners_are_nearly_square():
    rect = re.search(r"<rect\b[^>]*>", _svg()).group(0)
    assert int(re.search(r'rx="(\d+)"', rect).group(1)) <= 4, rect


def test_the_logo_url_carries_a_version():
    """An image is not a Frappe bundle, so its URL carries ?v=, bumped with every
    edit; a browser would otherwise keep showing the old tile."""
    import ast
    with open(os.path.join(APP_DIR, "hooks.py")) as f:
        tree = ast.parse(f.read())
    apps = next(ast.literal_eval(n.value) for n in tree.body
                if isinstance(n, ast.Assign) and getattr(n.targets[0], "id", "") == "add_to_apps_screen")
    assert re.fullmatch(r"/assets/konsol/images/konsol-logo\.svg\?v=\d{8}", apps[0]["logo"]), apps[0]["logo"]

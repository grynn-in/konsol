"""konsol's Desk CSS ships as Frappe build bundles (Deepak Pai, 7 Nov 2026: "yes,
do both", agreeing to bundle the CSS so browsers stop keeping old copies).

A plain ``/assets/konsol/css/x.css`` link has no version, so after a deploy a
browser keeps the old file until a hard refresh. A ``*.bundle.css`` under
``public/`` is built by Frappe's esbuild (esbuild/esbuild.js
get_all_files_to_build) into ``dist/css/<name>.<hash>.css``, and
``bundled_asset`` (utils/jinja_globals.py) resolves the bare name through
sites/assets/assets.json. Each build gives a new URL. The image builds them in
``bench get-app`` (konsolidat docker/frappe/Dockerfile) and bakes the manifest.
"""
import glob
import os

from konsol.tests.test_brand_colour import _hooks

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_every_included_stylesheet_is_a_bundle_by_its_bare_name():
    css = _hooks()["app_include_css"]
    assert css, css
    for name in css:
        assert not name.startswith("/"), f"{name}: a path is served unversioned; name the bundle"
        assert name.endswith(".bundle.css"), name
        assert os.path.exists(os.path.join(APP_DIR, "public", "css", name)), name


def test_no_plain_stylesheet_is_left_behind():
    """A leftover plain .css would be served too and could be edited by mistake."""
    plain = [os.path.relpath(p, APP_DIR) for p in glob.glob(os.path.join(APP_DIR, "public", "css", "*.css"))
             if not p.endswith(".bundle.css")]
    assert plain == [], plain


def test_bundle_names_are_unique_across_apps():
    """assets.json is keyed by the bare name, shared by every app; frappe's own
    are desk/website/report/... So konsol's carry the konsol_ prefix."""
    for name in _hooks()["app_include_css"]:
        assert name.startswith("konsol_"), name

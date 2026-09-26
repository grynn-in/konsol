"""The `/close` and `/close/<path>` routes serve the close-ui SPA shell to a
logged-in user, and redirect a Guest to log in first (D6).

Source-level assertions, mirroring test_exec_www.py: no frappe boot is
available on the host runner, so this reads the controller / template /
hooks source directly rather than issuing a request.
"""
import os

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WWW_DIR = os.path.join(APP_DIR, "www")
PY_PATH = os.path.join(WWW_DIR, "close.py")
HTML_PATH = os.path.join(WWW_DIR, "close.html")
HOOKS_PATH = os.path.join(APP_DIR, "hooks.py")


def _read(path):
    with open(path) as f:
        return f.read()


# ---- files exist (red: they are missing) --------------------------------

def test_controller_file_exists():
    assert os.path.exists(PY_PATH)


def test_template_file_exists():
    assert os.path.exists(HTML_PATH)


# ---- route rule -----------------------------------------------------------

def test_route_rule_is_present():
    hooks = _read(HOOKS_PATH)
    assert '{"from_route": "/close/<path:app_path>", "to_route": "close"}' in hooks


def test_konsol_exec_route_rule_is_removed():
    # konsol#305 R01 removed the old SPA and its route rule.
    hooks = _read(HOOKS_PATH)
    assert "/konsol-exec/" not in hooks


# ---- controller: guest redirect, csrf token, asset cache-buster ----------

def test_controller_redirects_guest_to_close_login():
    py = _read(PY_PATH)
    assert 'frappe.session.user == "Guest"' in py
    assert "/login?redirect-to=/close" in py


def test_controller_sets_real_csrf_token():
    py = _read(PY_PATH)
    assert "get_csrf_token()" in py
    assert "context.csrf_token" in py


def test_controller_has_no_mtime_query_cache_buster():
    # konsol#305 B26: cache-busting comes from the content hash in the file
    # name, not a `?v=` query (the query made the page load a second module
    # instance).
    py = _read(PY_PATH)
    assert "_asset_version" not in py
    assert "getmtime" not in py
    assert "build-manifest.json" in py


# ---- template loads the close bundle, not konsol-exec's ------------------

def test_template_bundle_urls_carry_no_query_string():
    html = _read(HTML_PATH)
    assert "?v=" not in html
    assert "js_version" not in html
    assert "css_version" not in html


def test_template_uses_context_csrf_token():
    html = _read(HTML_PATH)
    assert 'window.csrf_token = "{{ csrf_token }}"' in html


def test_template_does_not_reference_konsol_exec():
    html = _read(HTML_PATH)
    assert "konsol_exec" not in html
    assert "konsol-exec" not in html


# ---- rendered: the page loads exactly the manifest's entry (B26) ---------

BUNDLE_DIR = os.path.join(APP_DIR, "public", "close")


def _load_controller(app_dir):
    """Load www/close.py against a stub frappe whose get_app_path resolves
    under `app_dir` (the real app, or a scratch copy for failure paths)."""
    import importlib.util
    import sys
    import types

    frappe = types.ModuleType("frappe")
    frappe.ValidationError = type("ValidationError", (Exception,), {})
    frappe.Redirect = type("Redirect", (Exception,), {})

    def throw(msg, exc=None, **k):
        raise (exc or frappe.ValidationError)(msg)

    frappe.throw = throw
    frappe.get_app_path = lambda app, *parts: os.path.join(app_dir, *parts)
    frappe.session = types.SimpleNamespace(user="zz-b26@example.com")
    frappe.sessions = types.SimpleNamespace(get_csrf_token=lambda: "TOKEN")
    frappe.local = types.SimpleNamespace(flags=types.SimpleNamespace())

    saved = sys.modules.get("frappe")
    sys.modules["frappe"] = frappe
    try:
        spec = importlib.util.spec_from_file_location("close_www_under_test", PY_PATH)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        if saved is not None:
            sys.modules["frappe"] = saved
        else:
            sys.modules.pop("frappe", None)
    return module, frappe


def _render(context):
    """Substitute `{{ name }}` in close.html from the context (the host has
    no jinja; the template uses only plain variable references)."""
    import re

    html = _read(HTML_PATH)

    def sub(m):
        return str(getattr(context, m.group(1)))

    return re.sub(r"\{\{\s*(\w+)\s*\}\}", sub, html)


def _manifest():
    import json

    with open(os.path.join(BUNDLE_DIR, "build-manifest.json")) as f:
        return json.load(f)


def test_rendered_module_script_is_the_manifest_entry_with_no_query():
    import re
    import types

    module, _frappe = _load_controller(APP_DIR)
    context = types.SimpleNamespace()
    module.get_context(context)
    html = _render(context)
    srcs = re.findall(r'<script type="module" src="([^"]+)"', html)
    assert len(srcs) == 1, srcs
    src = srcs[0]
    assert "?" not in src, src
    entry = _manifest().get("entry")
    assert entry, "build-manifest.json names no entry"
    assert src == "/assets/konsol/close/" + entry
    assert os.path.isfile(os.path.join(BUNDLE_DIR, entry))


def test_rendered_stylesheet_is_the_manifest_css_with_no_query():
    import re
    import types

    module, _frappe = _load_controller(APP_DIR)
    context = types.SimpleNamespace()
    module.get_context(context)
    html = _render(context)
    hrefs = re.findall(r'<link rel="stylesheet" href="([^"]+)"', html)
    assert len(hrefs) == 1, hrefs
    href = hrefs[0]
    assert "?" not in href, href
    css = _manifest().get("css")
    assert css, "build-manifest.json names no css"
    assert href == "/assets/konsol/close/" + css
    assert os.path.isfile(os.path.join(BUNDLE_DIR, css))


def _scratch_app(manifest):
    import json
    import tempfile

    app_dir = tempfile.mkdtemp(prefix="zz-b26-")
    os.makedirs(os.path.join(app_dir, "public", "close"))
    if manifest is not None:
        with open(os.path.join(app_dir, "public", "close", "build-manifest.json"), "w") as f:
            json.dump(manifest, f)
    return app_dir


def _expect_refusal(app_dir, needle):
    import shutil
    import types

    try:
        module, frappe = _load_controller(app_dir)
        try:
            module.get_context(types.SimpleNamespace())
        except frappe.ValidationError as e:
            assert needle in str(e), str(e)
            assert "yarn build" in str(e), str(e)
        else:
            raise AssertionError("get_context served a page with no usable bundle")
    finally:
        shutil.rmtree(app_dir, ignore_errors=True)


def test_failure_path_missing_manifest_is_refused_naming_the_build():
    _expect_refusal(_scratch_app(None), "build-manifest.json")


def test_failure_path_manifest_without_entry_is_refused():
    _expect_refusal(_scratch_app({"srcHash": "x"}), "entry")


def test_failure_path_manifest_entry_file_missing_is_refused():
    _expect_refusal(
        _scratch_app({"srcHash": "x", "entry": "close.gone.js", "css": "close.gone.css"}),
        "close.gone.js",
    )

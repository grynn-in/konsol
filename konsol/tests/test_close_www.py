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


def test_konsol_exec_route_rule_is_not_removed():
    # R01 removes it, not this row.
    hooks = _read(HOOKS_PATH)
    assert '{"from_route": "/konsol-exec/<path:app_path>", "to_route": "konsol-exec"}' in hooks


# ---- controller: guest redirect, csrf token, asset cache-buster ----------

def test_controller_redirects_guest_to_close_login():
    py = _read(PY_PATH)
    assert 'frappe.session.user == "Guest"' in py
    assert "/login?redirect-to=/close" in py


def test_controller_sets_real_csrf_token():
    py = _read(PY_PATH)
    assert "get_csrf_token()" in py
    assert "context.csrf_token" in py


def test_controller_versions_assets_from_close_directory():
    py = _read(PY_PATH)
    assert "_asset_version" in py
    assert '"close"' in py
    assert "close.js" in py
    assert "close.css" in py


# ---- template loads the close bundle, not konsol-exec's ------------------

def test_template_loads_close_js_bundle():
    html = _read(HTML_PATH)
    assert "/assets/konsol/close/close.js" in html


def test_template_uses_context_csrf_token():
    html = _read(HTML_PATH)
    assert 'window.csrf_token = "{{ csrf_token }}"' in html


def test_template_does_not_reference_konsol_exec():
    html = _read(HTML_PATH)
    assert "konsol_exec" not in html
    assert "konsol-exec" not in html

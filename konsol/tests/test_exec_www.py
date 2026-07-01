"""The konsol-exec www page must load its controller and ship a real CSRF token.

Frappe maps a www template `konsol-exec.html` to a controller whose filename has
hyphens converted to underscores — `konsol_exec.py` (see
frappe/website/page_renderers/template_page.py `set_pymodule`). The controller
was named `konsol-exec.py` (hyphen), so Frappe never loaded it: `get_context`
never ran, the guest redirect / asset cache-buster were dead, and — worst —
`frappe.session.csrf_token` (which is None on web requests) was emitted verbatim,
so the standalone SPA POSTed the literal "None" and every state-changing call
403'd. These static assertions guard the fix.
"""
import os

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WWW_DIR = os.path.join(APP_DIR, "www")
PY_PATH = os.path.join(WWW_DIR, "konsol_exec.py")
HTML_PATH = os.path.join(WWW_DIR, "konsol-exec.html")


def _read(path):
    with open(path) as f:
        return f.read()


# ---- controller filename (the root-cause guard) ------------------------

def test_controller_uses_underscore_name():
    # Frappe converts the route's hyphens to underscores to find the module.
    assert os.path.exists(PY_PATH)


def test_hyphenated_controller_is_gone():
    assert not os.path.exists(os.path.join(WWW_DIR, "konsol-exec.py"))


# ---- csrf token is real, not the None-valued session attribute ---------

def test_controller_sets_real_csrf_token():
    py = _read(PY_PATH)
    assert "get_csrf_token()" in py
    assert "context.csrf_token" in py


def test_template_uses_context_csrf_token():
    html = _read(HTML_PATH)
    assert 'window.csrf_token = "{{ csrf_token }}"' in html
    # the old None-valued accessor must not linger
    assert "frappe.session.csrf_token" not in html


# ---- controller still guards guest access + busts asset cache ----------

def test_controller_redirects_guest_and_versions_assets():
    py = _read(PY_PATH)
    assert 'frappe.session.user == "Guest"' in py
    assert "_asset_version" in py

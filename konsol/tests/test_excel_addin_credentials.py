"""The Excel add-in must never send cookies with its API calls.

Excel Online hosts the task pane in a cross-site iframe. When Chrome grants the
frame partitioned-cookie access, `credentials: "include"` authenticates the
request by cookie, so frappe.session.user is no longer Guest —
apply_excel_token_auth() (a before_request hook that only fires for Guest)
returns early, and the request becomes a plain cookie POST. Frappe requires an
X-Frappe-CSRF-Token for those and the add-in has none, so it fails with
CSRFTokenError -> HTTP 400.

The failure is intermittent by nature: it appears only once the browser decides
to honour the cookie, so the same build works in incognito and fails in a normal
tab. These tests pin the invariant so it cannot regress silently.
"""
import os
import re

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ADDIN_DIR = os.path.join(APP_DIR, "public", "excel-addin")

_CREDENTIALS = re.compile(r"credentials\s*[:=][^,;\n]*", re.I)


def _read(name):
    with open(os.path.join(ADDIN_DIR, name)) as handle:
        return handle.read()


def test_no_call_site_sends_cookies():
    for name in ("index.html", "functions.js"):
        for match in _CREDENTIALS.findall(_read(name)):
            assert "include" not in match, (
                f"{name}: {match.strip()} — sending cookies triggers Frappe's "
                "CSRF check (400); the add-in authenticates with "
                "X-Konsolidat-Token instead"
            )


def test_api_helper_defaults_to_omit():
    assert 'opts.credentials = opts.credentials || "omit"' in _read("index.html")


def test_custom_functions_post_omits_credentials():
    source = _read("functions.js")
    assert 'credentials: "omit"' in source
    # The token header is what actually authenticates these calls.
    assert "X-Konsolidat-Token" in source

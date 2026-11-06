"""The Excel add-in must not send the browser's Desk session cookie (6 Nov 2026).

Found live on demo.konsolidat.com: Snapshot and =K.EPM answered "HTTP 400".
The add-in fetched with credentials "include", so once the user had opened the
Desk in the same browser, the request carried a Desk session that holds a CSRF
token, and Frappe refused every POST without X-Frappe-CSRF-Token
(CSRFTokenError, HTTP 400). Reproduced on the 8069 test stack: 200 before the
Desk is opened, 400 after.

The add-in signs in with its own token (X-Konsolidat-Token), so a request that
has the token sends no cookie. And an error names Frappe's message, never a
bare "HTTP 400".
"""
import os
import re

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ADDIN = os.path.join(APP_DIR, "public", "excel-addin")


def _read(name):
    with open(os.path.join(ADDIN, name)) as f:
        return f.read()


def _body(src, head):
    start = src.index(head)
    end = src.find("\n      function ", start + 1)
    if end < 0:
        end = src.find("\nfunction ", start + 1)
    return src[start:end]


def test_pane_api_omits_cookies_when_it_has_a_token():
    body = _body(_read("index.html"), "function api(")
    assert re.search(r'credentials\s*=\s*opts\.credentials\s*\|\|\s*\(token\s*\?\s*"omit"\s*:\s*"include"\)', body), \
        "api() must omit the browser's cookies when the add-in has a token"


def test_custom_functions_omit_cookies_when_they_have_a_token():
    src = _read("functions.js")
    start = src.index("function postJson(")
    body = src[start:src.index("\nfunction ", start + 1)]
    assert 'credentials: "include"' not in body, "postJson sends the Desk cookie, so a Desk session's CSRF check refuses it"
    assert re.search(r'credentials:\s*hasToken\(\)\s*\?\s*"omit"\s*:\s*"include"', body), body


def test_errors_name_frappes_message_not_a_bare_status():
    body = _body(_read("index.html"), "function parseApiError(")
    assert "_server_messages" in body and "exc_type" in body, body


def test_an_expired_token_brings_back_the_sign_in_form():
    body = _body(_read("index.html"), "function api(")
    assert re.search(r"res\.status === 401 \|\| res\.status === 403", body), body
    assert "whoami()" in body and 'setToken("")' in body and "showLogin()" in body, body

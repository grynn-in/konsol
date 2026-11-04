"""The Excel add-in's CookieManager.set_cookie wrapper (konsol/excel_addin_cookies.py).

Frappe v16 added a ``deduplicate`` keyword to ``CookieManager.set_cookie``.
The wrapper had copied the v15 signature, so every request that set a cookie
raised ``TypeError: _excel_set_cookie() got an unexpected keyword argument
'deduplicate'`` inside ``init_request``. The wrapper must pass through any
argument the framework's own method takes, and still force SameSite=None;
Secure on /api/ requests.
"""
import importlib
import os
import sys
import types

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load(calls):
    """Import excel_addin_cookies against a stub frappe whose set_cookie has
    the v16 signature, recording what reaches it."""

    class CookieManager:
        def set_cookie(self, key, value, expires=None, secure=False, httponly=False,
                       samesite="Lax", max_age=None, deduplicate=False):
            calls.append(dict(key=key, value=value, secure=secure, samesite=samesite,
                              deduplicate=deduplicate, max_age=max_age))

    frappe = types.ModuleType("frappe")
    frappe.local = types.SimpleNamespace(request=None)
    auth = types.ModuleType("frappe.auth")
    auth.CookieManager = CookieManager
    frappe.auth = auth
    saved = {k: sys.modules.get(k) for k in ("frappe", "frappe.auth", "konsol.excel_addin_cookies")}
    sys.modules["frappe"] = frappe
    sys.modules["frappe.auth"] = auth
    sys.modules.pop("konsol.excel_addin_cookies", None)
    sys.path.insert(0, os.path.dirname(APP_DIR))
    try:
        importlib.import_module("konsol.excel_addin_cookies")
    finally:
        sys.path.pop(0)
        for k, v in saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v
    return frappe, CookieManager


def test_wrapper_accepts_and_forwards_the_v16_deduplicate_keyword():
    calls = []
    _frappe, CookieManager = _load(calls)
    CookieManager().set_cookie("sid", "abc", deduplicate=True, max_age=60)
    assert calls == [dict(key="sid", value="abc", secure=False, samesite="Lax",
                          deduplicate=True, max_age=60)]


def test_api_requests_still_get_samesite_none_and_secure():
    calls = []
    frappe, CookieManager = _load(calls)
    frappe.local.request = types.SimpleNamespace(path="/api/method/ping")
    CookieManager().set_cookie("sid", "abc", deduplicate=True)
    assert calls[0]["samesite"] == "None" and calls[0]["secure"] is True
    assert calls[0]["deduplicate"] is True

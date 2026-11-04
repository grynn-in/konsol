"""Session cookies for the Excel add-in iframe (Excel Online on office.com)."""

import frappe
from frappe.auth import CookieManager

_original_set_cookie = CookieManager.set_cookie


def _excel_set_cookie(self, key, value, *args, **kwargs):
    """Force SameSite=None; Secure on /api/ cookies, and pass every other
    argument through untouched. Copying the framework's signature broke on
    Frappe v16, which added ``deduplicate``."""
    request = getattr(frappe.local, "request", None)
    if request is not None and request.path.startswith("/api/"):
        kwargs["samesite"] = "None"
        kwargs["secure"] = True
    return _original_set_cookie(self, key, value, *args, **kwargs)


CookieManager.set_cookie = _excel_set_cookie


def add_partitioned_cookie_headers():
    """Chrome requires Partitioned on SameSite=None cookies in embedded iframes."""
    request = getattr(frappe.local, "request", None)
    if request is None or not request.path.startswith("/api/"):
        return
    response = getattr(frappe.local, "response", None)
    if response is None or not hasattr(response, "headers"):
        return
    try:
        cookies = response.headers.getlist("Set-Cookie")
    except Exception:
        return
    if not cookies:
        return
    response.headers.pop("Set-Cookie", None)
    for cookie in cookies:
        if "Partitioned" not in cookie:
            cookie = cookie + "; Partitioned"
        response.headers.add("Set-Cookie", cookie)
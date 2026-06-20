"""Session cookies for the Excel add-in iframe (Excel Online on office.com)."""

import frappe
from frappe.auth import CookieManager

_original_set_cookie = CookieManager.set_cookie


def _excel_set_cookie(
    self,
    key,
    value,
    expires=None,
    secure=False,
    httponly=False,
    samesite="Lax",
    max_age=None,
):
    request = getattr(frappe.local, "request", None)
    if request is not None and request.path.startswith("/api/"):
        samesite = "None"
        secure = True
    return _original_set_cookie(
        self,
        key,
        value,
        expires=expires,
        secure=secure,
        httponly=httponly,
        samesite=samesite,
        max_age=max_age,
    )


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
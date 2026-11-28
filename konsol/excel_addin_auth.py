"""Bearer-token auth for Excel Online iframe (cookies often blocked)."""

import frappe

_TOKEN_PREFIX = "konsol:excel_token:"
_TOKEN_TTL = 8 * 3600  # 8 hours


def apply_excel_token_auth():
    """If request carries X-Konsolidat-Token, act as that user (Guest sessions only)."""
    if frappe.session.user != "Guest":
        return
    token = (frappe.get_request_header("X-Konsolidat-Token") or "").strip()
    if not token:
        return
    data = frappe.cache().get_value(_TOKEN_PREFIX + token)
    if data and data.get("user"):
        # frappe.set_user() resets frappe.local.form_dict = _dict()
        # (frappe/__init__.py ~388, 16.55.2), but frappe/app.py builds form_dict
        # (make_form_dict, ~:214) BEFORE before_request hooks run (~:219). Without
        # this, every token GET loses its query args (report_choices?kind=...).
        form_dict = frappe.local.form_dict
        frappe.set_user(data["user"])
        frappe.local.form_dict = form_dict


def issue_token(user):
    token = frappe.generate_hash(length=40)
    frappe.cache().set_value(
        _TOKEN_PREFIX + token,
        {"user": user},
        expires_in_sec=_TOKEN_TTL,
    )
    return token


def revoke_token(token):
    if token:
        frappe.cache().delete_value(_TOKEN_PREFIX + token)
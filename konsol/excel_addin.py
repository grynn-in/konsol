"""The Excel add-in's per-site manifest and its cross-origin access (konsol#605).

Decided by Deepak Pai, 27 Nov 2026 (konsol#605): build A and B. The rules are
in ``konsol/excel_addin_model.py``; this is the frappe glue.
"""
import os

import frappe
from frappe.utils import escape_html

from konsol import excel_addin_model as model

_TEMPLATE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "excel_addin_manifest.template.xml")


# allow_guest: the manifest holds nothing but the site's public address, as
# the static file under /assets did before. Microsoft 365's centralized
# deployment can take the manifest by URL ("Provide link to the manifest
# file"), and Microsoft's service fetches it without a konsol session; a Desk
# user downloads it signed in. GET only: it writes nothing.
@frappe.whitelist(allow_guest=True, methods=["GET"])
def manifest():
    """The add-in manifest for this site, as ``konsolidat-<site>.xml``, built
    from the site's host_name. A site without a usable host_name gets a
    refusal page (409) that says so."""
    try:
        base = model.site_base_url(frappe.conf.get("host_name"))
    except ValueError as e:
        frappe.respond_as_web_page(
            "Excel add-in not available",
            "<p>%s</p>" % escape_html(str(e)),
            http_status_code=409,
            indicator_color="red",
        )
        return
    with open(_TEMPLATE, encoding="utf-8") as f:
        xml = model.render_manifest(f.read(), base)
    frappe.response["type"] = "download"
    frappe.response["filename"] = model.manifest_filename(frappe.local.site)
    frappe.response["filecontent"] = xml
    frappe.response["content_type"] = "application/xml"


def add_cors_headers(response=None, request=None):
    """after_request hook: CORS headers for the add-in endpoints, from Close
    Settings → Excel Add-in Origins.

    Frappe answers a preflight (OPTIONS) with an empty 200 and runs the
    after_request hooks on every response, errors included (frappe/app.py,
    v16.55.2), so a listed origin can also read a refusal's message.

    Frappe's own ``allow_cors`` (site config) is switched off on these paths
    by setting ``frappe.local.allow_cors = None``: its set_cors_headers runs
    after this hook and always adds ``Access-Control-Allow-Credentials:
    true``, which the add-in must never get (it sends a token, no cookies).
    """
    request = request or getattr(frappe.local, "request", None)
    if request is None or response is None or request.path not in model.ADDIN_PATHS:
        return
    frappe.local.allow_cors = None
    origin = request.headers.get("Origin")
    if not origin:
        return
    headers = model.cors_headers(request.path, request.method, origin, _allowed_origins())
    for key, value in headers.items():
        response.headers[key] = value


def _allowed_origins():
    """The listed origins. The list is validated on save; one that no longer
    parses allows nothing, and is logged."""
    text = getattr(frappe.get_cached_doc("Close Settings"), "excel_addin_origins", None)
    try:
        return model.parse_origin_list(text)
    except ValueError as e:
        frappe.log_error(title="Excel Add-in Origins do not parse", message=str(e))
        return ()

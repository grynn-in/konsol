import os

import frappe

no_cache = 1


def _asset_version(filename):
	"""mtime-based cache-buster for the (non-content-hashed) SPA bundle, so a new
	build is always fetched without a manual hard refresh."""
	try:
		path = frappe.get_app_path("konsol", "public", "konsol_exec", filename)
		return str(int(os.path.getmtime(path)))
	except OSError:
		return "0"


def get_context(context):
	if frappe.session.user == "Guest":
		frappe.local.flags.redirect_location = "/login?redirect-to=/konsol-exec"
		raise frappe.Redirect

	context.no_cache = 1
	context.no_header = 1
	context.no_footer = 1
	context.no_sidebar = 1
	context.full_width = 1
	context.js_version = _asset_version("konsol_exec.js")
	context.css_version = _asset_version("konsol_exec.css")
	# Real per-session CSRF token for the standalone SPA's POST calls. Frappe's
	# `frappe.session.csrf_token` attribute is unset (None) on web requests — the
	# token is generated on demand — so the SPA was posting the literal "None"
	# and every state-changing call 403'd. get_csrf_token() generates + returns it.
	context.csrf_token = frappe.sessions.get_csrf_token()
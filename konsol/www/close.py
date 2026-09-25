import json
import os

import frappe

no_cache = 1

#: URL prefix of the committed bundle (close-ui's vite `base`).
ASSET_BASE = "/assets/konsol/close/"


def _bundle_files():
	"""The content-hashed entry script and stylesheet, as recorded by
	close-ui's build in build-manifest.json (konsol#305 B26).

	The page must load the entry by EXACTLY the name the lazy chunks import
	(`./close.<hash>.js`). A `?v=` cache-buster made the URLs differ, so the
	browser ran the module twice and a period screen rendered blank. The
	content hash now does the cache-busting, so no query string is added.

	A missing or incomplete manifest is refused with the fix, never replaced
	by a guessed file name."""
	bundle_dir = frappe.get_app_path("konsol", "public", "close")
	manifest_path = os.path.join(bundle_dir, "build-manifest.json")
	build_hint = " Run `cd close-ui && yarn build` and deploy the rebuilt konsol/public/close."
	try:
		with open(manifest_path) as f:
			manifest = json.load(f)
	except (OSError, ValueError):
		frappe.throw(
			"The close app is not built: konsol/public/close/build-manifest.json is missing or unreadable."
			+ build_hint
		)
	files = {}
	for key in ("entry", "css"):
		name = manifest.get(key)
		if not name:
			frappe.throw(
				"The close app build is incomplete: build-manifest.json names no {0} file.".format(key)
				+ build_hint
			)
		if not os.path.isfile(os.path.join(bundle_dir, name)):
			frappe.throw(
				"The close app build is incomplete: build-manifest.json names {0}, which is not in konsol/public/close.".format(
					name
				)
				+ build_hint
			)
		files[key] = ASSET_BASE + name
	return files


def get_context(context):
	if frappe.session.user == "Guest":
		frappe.local.flags.redirect_location = "/login?redirect-to=/close"
		raise frappe.Redirect

	context.no_cache = 1
	context.no_header = 1
	context.no_footer = 1
	context.no_sidebar = 1
	context.full_width = 1
	files = _bundle_files()
	context.close_js = files["entry"]
	context.close_css = files["css"]
	# Real per-session CSRF token for the SPA's POST calls. Frappe's
	# `frappe.session.csrf_token` attribute is unset (None) on web requests — the
	# token is generated on demand — so get_csrf_token() generates + returns it.
	context.csrf_token = frappe.sessions.get_csrf_token()

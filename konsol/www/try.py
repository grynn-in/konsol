"""`/try`: the demo mode landing page, one button into the demo.

Served only while demo mode is on (see konsol/demo_mode.py); otherwise the
visitor gets Frappe's 404, so a site without a demo shows nothing of it.
"""
import frappe

from konsol import demo_mode

no_cache = 1

#: The Guest GET that logs the visitor in and redirects to the Desk.
TRY_URL = "/api/method/konsol.demo_mode.try_demo"


def get_context(context):
    if not demo_mode.decision().allowed:
        raise frappe.PageDoesNotExistError
    context.no_cache = 1
    context.no_header = 1
    context.no_footer = 1
    context.no_sidebar = 1
    context.try_url = TRY_URL

"""Delete the Konsol Home Dashboard Custom HTML Block (trial, 8 Nov 2026).

The Close dashboard became a standard Page, ``konsol-dashboard``, built like
Konsol Setup, so the block it replaced is no longer shipped or installed. The
record exists only on a site that ran the trial; a missing record is skipped.
"""
import frappe

BLOCK = "Konsol Home Dashboard"


def execute():
    if frappe.db.exists("Custom HTML Block", BLOCK):
        frappe.delete_doc("Custom HTML Block", BLOCK, force=True, ignore_permissions=True)

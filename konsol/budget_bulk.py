"""Bulk budget upload: the check endpoint (konsol#348 U1). Mirrors
konsol.tb_bulk.check_file / check_upload.

The check reads the attached file, hands the table to
``budget_intake.plan`` (the one entry every budget writer calls) and writes
the counts and the per-(scenario, year, entity, layer, period) report onto the
Budget Upload. A refused group is named in the report with its refusals. The
check writes no Budget Entry; the load (U2) does.
"""
import json

import frappe

from konsol import budget_intake
from konsol.tb_bulk import _read_table

DOCTYPE = "Budget Upload"


def _require_uploader():
    if not frappe.has_permission(DOCTYPE, "create"):
        frappe.throw("Uploading budgets in bulk needs permission to create a Budget Upload.",
                     frappe.PermissionError)


def _record_check(doc):
    """Check the file and write the report onto the upload."""
    try:
        report = budget_intake.plan(_read_table(doc.upload_file),
                                    form_basis=doc.amount_basis or "")["report"]
    except ValueError as e:
        doc.status, doc.error, doc.report = "Failed", str(e), "[]"
        doc.group_count = doc.valid_count = doc.total_rows = 0
    else:
        doc.report = json.dumps(report)
        doc.group_count = len(report)
        doc.valid_count = sum(1 for r in report if r["ok"])
        doc.total_rows = sum(r["rows"] for r in report)
        doc.status, doc.error = "Checked", ""
    doc.loaded_count = doc.failed_count = 0
    doc.save()


def _payload(doc):
    return {
        "name": doc.name, "status": doc.status, "file_url": doc.upload_file,
        "amount_basis": doc.amount_basis or "",
        "group_count": doc.group_count, "valid_count": doc.valid_count, "total_rows": doc.total_rows,
        "loaded_count": doc.loaded_count, "failed_count": doc.failed_count, "error": doc.error,
        "report": json.loads(doc.report or "[]"),
    }


@frappe.whitelist(methods=["POST"])
def check_file(file_url, amount_basis=None):
    """Create an upload for an uploaded file and report on it. Loads nothing.

    `amount_basis` is the upload's Amount Basis for every group whose rows do
    not give their own; such a group is otherwise reported as refused.
    """
    _require_uploader()
    doc = frappe.get_doc({"doctype": DOCTYPE, "upload_file": file_url, "status": "Draft",
                          "amount_basis": amount_basis or ""}).insert()
    _record_check(doc)
    return _payload(doc)


@frappe.whitelist(methods=["POST"])
def check_upload(name, amount_basis=None):
    """Check a saved upload in place and report on it. Loads nothing.

    The Desk form's Check button: the upload already exists with its file
    attached. A loading or loaded upload is refused: its report is its outcome.
    """
    _require_uploader()
    doc = frappe.get_doc(DOCTYPE, name)
    doc.check_permission("write")
    if doc.status not in ("Draft", "Checked", "Failed"):
        frappe.throw(f"This upload is {doc.status.lower()}; it cannot be checked again.")
    if amount_basis:
        doc.amount_basis = amount_basis
    _record_check(doc)
    return _payload(doc)

"""Bulk trial balance upload: one file, many entity-periods.

Flow (konsol-exec's upload page, or the Trial Balance Upload form):

  1. The file is uploaded as a private File, then `check_file` creates a
     Trial Balance Upload and reports on every entity-period in it. Nothing
     is loaded; the report says which are ready and why the others are not.
  2. `load` re-checks (a period may have closed, or someone submitted in
     between) and queues `run_load`, refusing a file with problems unless the
     user chose to load only the ready ones.
  3. `run_load` (a background job, run as the user who asked) creates one
     ordinary Trial Balance Submission per ready entity-period and submits
     it. Each one commits on its own: its rows are in the warehouse once
     submitted, so a later failure must not roll it back. Progress is
     written to the upload as it goes.

Only a user who may submit trial balances (EPM Admin, System Manager) may
load; the per-entity access check still applies to every row.
"""
import json

import frappe
from frappe.utils import cint, now_datetime, strip_html

from konsol import period_status
from konsol import tb_bulk_model as M
from konsol.clickhouse import execute
from konsol.consolidation.doctype.trial_balance_submission.trial_balance_submission import validate_tb_rows

DOCTYPE = "Trial Balance Upload"
TERMINAL = ("Loaded", "Partly Loaded", "Failed")


def _require_loader():
    if not (frappe.has_permission("Trial Balance Submission", "submit")
            and frappe.has_permission(DOCTYPE, "create")):
        frappe.throw("Loading trial balances in bulk needs the Close Lead role (EPM Admin).",
                     frappe.PermissionError)


def _read_table(file_url):
    file_doc = frappe.get_doc("File", {"file_url": file_url})
    if not frappe.has_permission("File", "read", doc=file_doc):
        frappe.throw("You cannot read that file.", frappe.PermissionError)
    name = (file_doc.file_name or file_url).lower()
    content = file_doc.get_content()
    if name.endswith(".xlsx"):
        from frappe.utils.xlsxutils import read_xlsx_file_from_attached_file
        if isinstance(content, str):
            content = content.encode("latin-1")
        return read_xlsx_file_from_attached_file(fcontent=content, read_only=True) or []
    if name.endswith((".csv", ".txt")):
        if isinstance(content, bytes):
            content = content.decode("utf-8-sig")
        return M.table_from_csv(content)
    frappe.throw("Upload a .csv or .xlsx file.")


def _chart_accounts():
    """The group chart, strictly: a warehouse outage refuses the check rather
    than passing unverified accounts (the single-upload rule)."""
    try:
        text = execute("SELECT DISTINCT main_account_id FROM epm_silver.silver_main_accounts")
    except Exception as e:
        frappe.throw(f"Cannot check accounts against the group chart: ClickHouse is unreachable ({e}). "
                     "Try again once the warehouse is up.")
    return {line.strip() for line in text.splitlines() if line.strip()}


def _check(table):
    groups = M.split_table(table)
    entities = sorted({k[0] for k in groups})
    # get_list applies the uploader's entity scope; get_all would not.
    visible = set(frappe.get_list("Entity", filters={"name": ["in", entities]}, pluck="name",
                                  limit_page_length=0))
    leaf = set(frappe.get_all("Entity", filters={"name": ["in", entities], "is_group": 0}, pluck="name"))
    statuses = {(y, p): period_status.get_status(y, p) for (_, y, p) in groups if 1 <= p <= 12}
    existing = {}
    for r in frappe.get_all("Trial Balance Submission",
                            filters={"docstatus": 1, "data_area_id": ["in", entities]},
                            fields=["name", "data_area_id", "fiscal_year", "fiscal_period"],
                            limit_page_length=0):
        existing[(r.data_area_id, int(r.fiscal_year), int(r.fiscal_period))] = r.name
    chart = _chart_accounts()
    report = [
        M.check_group(key, rows, known_accounts=chart, visible=key[0] in visible, leaf=key[0] in leaf,
                      period_status=statuses.get((key[1], key[2])), existing=existing.get(key),
                      validate_rows=validate_tb_rows)
        for key, rows in groups.items()
    ]
    return groups, report


def _record_check(doc):
    """Check the file and write the report onto the upload."""
    try:
        _, report = _check(_read_table(doc.upload_file))
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
        "file_name": (doc.upload_file or "").rsplit("/", 1)[-1],
        "group_count": doc.group_count, "valid_count": doc.valid_count, "total_rows": doc.total_rows,
        "loaded_count": doc.loaded_count, "failed_count": doc.failed_count, "error": doc.error,
        "report": json.loads(doc.report or "[]"), "owner": frappe.utils.get_fullname(doc.owner),
        "creation": str(doc.creation),
    }


@frappe.whitelist(methods=["POST"])
def check_file(file_url):
    """Create an upload for an uploaded file and report on it. Loads nothing."""
    _require_loader()
    doc = frappe.get_doc({"doctype": DOCTYPE, "upload_file": file_url, "status": "Draft"}).insert()
    _record_check(doc)
    return _payload(doc)


@frappe.whitelist(methods=["POST"])
def load(name, skip_invalid=0):
    """Re-check, then queue the load of every ready entity-period."""
    _require_loader()
    doc = frappe.get_doc(DOCTYPE, name)
    doc.check_permission("write")
    if doc.status != "Checked":
        frappe.throw(f"This upload is {doc.status.lower()}. Check the file again to load it.")
    _record_check(doc)
    report = json.loads(doc.report or "[]")
    ready = sum(1 for r in report if r["ok"])
    problems = len(report) - ready
    if not ready:
        frappe.throw("Nothing in this file is ready to load.")
    if problems and not cint(skip_invalid):
        frappe.throw(f"{problems} of {len(report)} entity-periods have problems. Fix the file, "
                     f"or load only the {ready} that are ready.")
    doc.status = "Loading"
    doc.save()
    frappe.enqueue("konsol.tb_bulk.run_load", queue="long", timeout=3600,
                   job_id=f"konsol-tb-upload::{doc.name}", deduplicate=True,
                   enqueue_after_commit=True, upload=doc.name)
    return _payload(doc)


@frappe.whitelist(methods=["GET"])
def get_upload(name):
    doc = frappe.get_doc(DOCTYPE, name)
    doc.check_permission("read")
    return _payload(doc)


@frappe.whitelist(methods=["GET"])
def recent_uploads(limit=8):
    return frappe.get_list(DOCTYPE, fields=["name", "status", "upload_file", "group_count", "valid_count",
                                            "loaded_count", "failed_count", "owner", "creation"],
                           order_by="creation desc", limit_page_length=min(cint(limit) or 8, 50))


def _save_progress(name, report, loaded, failed, status=None, error=None):
    values = {"report": json.dumps(report), "loaded_count": loaded, "failed_count": failed}
    if status:
        values["status"] = status
    if error is not None:
        values["error"] = error
    frappe.db.set_value(DOCTYPE, name, values, update_modified=bool(status))
    frappe.db.commit()


def run_load(upload):
    """Background job: one Trial Balance Submission per ready entity-period.

    Runs as the user who asked (Frappe sets the job's user), so permissions
    and entity scope apply to every row. Each entity-period commits on its
    own; a failure is recorded on that row and the rest carry on.
    """
    doc = frappe.get_doc(DOCTYPE, upload)
    report = json.loads(doc.report or "[]")
    loaded = failed = 0
    try:
        groups = M.split_table(_read_table(doc.upload_file))
        ready = [r for r in report if r["ok"]]
        for i, item in enumerate(ready, start=1):
            key = (item["entity"], int(item["fiscal_year"]), int(item["fiscal_period"]))
            try:
                file_doc = frappe.get_doc({
                    "doctype": "File", "is_private": 1,
                    "file_name": f"{doc.name}-{key[0]}-{key[1]}-P{key[2]:02d}.csv",
                    "content": M.group_csv(groups[key]),
                }).insert()
                tbs = frappe.get_doc({"doctype": "Trial Balance Submission", "data_area_id": key[0],
                                      "fiscal_year": key[1], "fiscal_period": key[2],
                                      "tb_file": file_doc.file_url}).insert()
                # Attach before submitting: on_submit lands the rows in the
                # warehouse, so nothing may fail between it and the commit.
                frappe.db.set_value("File", file_doc.name,
                                    {"attached_to_doctype": "Trial Balance Submission",
                                     "attached_to_name": tbs.name}, update_modified=False)
                tbs.submit()
                frappe.db.commit()
                item["loaded"] = tbs.name
                loaded += 1
            except Exception as e:
                frappe.db.rollback()
                frappe.clear_messages()
                item["load_error"] = strip_html(str(e)) or type(e).__name__
                failed += 1
            if i % 10 == 0:
                _save_progress(doc.name, report, loaded, failed)
        _save_progress(doc.name, report, loaded, failed, status=M.outcome(loaded, failed, len(ready)))
    except Exception as e:
        frappe.db.rollback()
        frappe.log_error(title=f"Trial balance upload {doc.name} failed")
        _save_progress(doc.name, report, loaded, failed, status="Partly Loaded" if loaded else "Failed",
                       error=strip_html(str(e)) or type(e).__name__)

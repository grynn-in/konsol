"""Bulk budget upload: check (konsol#348 U1) and load (U2). Mirrors
konsol.tb_bulk.

Flow (the Budget Upload form):

  1. ``check_file`` / ``check_upload`` read the attached file, hand the table
     to ``budget_intake.plan`` (the one entry every budget writer calls) and
     write the counts and the per-(scenario, year, entity, layer, period)
     report onto the Budget Upload. A refused group is named in the report
     with its refusals. The check writes no Budget Entry.
  2. ``load`` re-checks (a Cycle may have been approved, a role taken away).
     If problems appeared and the user did not choose to skip them, it
     returns the fresh report with a `refused` reason. Otherwise it queues
     ``run_load`` after the commit.
  3. ``run_load`` (a background job, run as the user who asked) judges the
     file again and replaces every ready slice. A slice is one (cycle,
     entity, layer): the file's periods for it each become one Budget Entry
     (created, or the existing one's lines replaced), and the slice's entries
     for periods absent from the file are deleted. A slice loads whole or not
     at all: one refused period holds back its other periods, and the report
     says so. Refused groups are never loaded. Each slice commits on its own;
     a failure rolls back that slice only and the rest carry on.
  4. Replacing is idempotent, so a load that stopped or finished partly is
     resumed by loading again: a reload replaces and never doubles.

Nothing here syncs to the warehouse: an entry changes only under a Draft
Cycle, and the Cycle's approval syncs its lines (T2).

The loaded count is never stored (as the trial balance upload, konsol#332-2):
``upload_state`` derives it from the entries that still name this upload.
"""
import json
import time

import frappe
from frappe.utils import cint, strip_html

from konsol import budget_intake
from konsol.tb_bulk import _read_table

DOCTYPE = "Budget Upload"
ENTRY = "Budget Entry"
JOB_PREFIX = "konsol-budget-upload::"
JOB_TIMEOUT = 3600
#: The job stops itself this long after starting, well inside JOB_TIMEOUT, so
#: RQ never has to kill it in the middle of a slice.
TIME_BUDGET_SECONDS = 3000


def _job_id(name):
    return f"{JOB_PREFIX}{name}"


def _job_running(name):
    """True while the load job is queued or running. When RQ cannot be asked,
    assume it is running: a resume must never start a second load."""
    try:
        from frappe.utils.background_jobs import is_job_enqueued
        return bool(is_job_enqueued(_job_id(name)))
    except Exception:
        return True


def _require_uploader():
    if not frappe.has_permission(DOCTYPE, "create"):
        frappe.throw("Uploading budgets in bulk needs permission to create a Budget Upload.",
                     frappe.PermissionError)


def _require_loader():
    if not (frappe.has_permission(ENTRY, "create") and frappe.has_permission(DOCTYPE, "create")):
        frappe.throw("Loading budgets in bulk needs permission to create a Budget Upload and a Budget Entry.",
                     frappe.PermissionError)


# ── slices ─────────────────────────────────────────────────────────────────

def _slice(item):
    """A report item's slice. (scenario, year) names the Cycle."""
    return (item["scenario"], int(item["fiscal_year"]), item["entity"], item["layer"])


def _whole_slices(report):
    """The report with every ready period of a slice that has a refused
    period marked not ready, naming the refused periods: a slice is replaced
    whole, so loading part of it would delete or keep periods the file did
    not mean to."""
    refused = {}
    for item in report:
        if not item["ok"]:
            refused.setdefault(_slice(item), []).append(int(item["fiscal_period"]))
    out = []
    for item in report:
        periods = refused.get(_slice(item))
        if item["ok"] and periods:
            named = ", ".join(str(p) for p in sorted(periods))
            item = {**item, "ok": False, "errors": list(item["errors"]) + [
                f"Not loaded: entity {item['entity']}, layer {item['layer']} is refused in "
                f"period{'s' if len(periods) > 1 else ''} {named}, and an entity's layer is loaded "
                "for all its periods or none. Fix the refused period, or remove it from the file."]}
        out.append(item)
    return out


# ── checking ───────────────────────────────────────────────────────────────

def _counts(doc, report):
    doc.group_count = len(report)
    doc.valid_count = sum(1 for r in report if r["ok"])
    doc.total_rows = sum(r["rows"] for r in report)


def _record_check(doc):
    """Check the file and write the report onto the upload."""
    try:
        report = budget_intake.plan(_read_table(doc.upload_file),
                                    form_basis=doc.amount_basis or "", name_lines=True)["report"]
    except ValueError as e:
        doc.status, doc.error, doc.report = "Failed", str(e), "[]"
        doc.group_count = doc.valid_count = doc.total_rows = 0
    else:
        report = _whole_slices(report)
        doc.report = json.dumps(report)
        _counts(doc, report)
        doc.status, doc.error = "Checked", ""
    doc.failed_count = 0
    doc.save()


# ── the upload as it stands ────────────────────────────────────────────────

def upload_state(doc):
    """The upload's report, loaded count and status as they are now: derived
    from the Budget Entries that still name this upload, never stored (the
    trial balance upload's rule, konsol#332-2). An entry a later upload
    replaced, or one deleted, lowers the count when read.

    The stored status is the load job's own state. A finished load (Loaded,
    Partly Loaded) is only ever demoted: Partly Loaded while any of its
    entries stand, else Superseded (derived only, never stored)."""
    report = json.loads(doc.report or "[]")
    claimed = [r["loaded"] for r in report if r.get("loaded")]
    standing = set(frappe.get_all(ENTRY, filters={"name": ["in", sorted(set(claimed))],
                                                  "budget_upload": doc.name},
                                  pluck="name", order_by="name asc", limit_page_length=0)) if claimed else set()
    now = []
    for r in report:
        if r.get("loaded") and r["loaded"] not in standing:
            r = {**{k: v for k, v in r.items() if k != "loaded"}, "was_loaded": r["loaded"],
                 "superseded": f"Loaded as {r['loaded']}, which another write has since replaced or deleted."}
        now.append(r)
    loaded = sum(1 for r in now if r.get("loaded"))
    status = doc.status
    if status in ("Loaded", "Partly Loaded") and loaded < len(claimed):
        status = "Partly Loaded" if loaded else "Superseded"
    return {"report": now, "loaded_count": loaded, "status": status}


def _payload(doc, refused=None, refused_kind=None):
    state = upload_state(doc)
    return {
        "name": doc.name, "status": state["status"], "file_url": doc.upload_file,
        "amount_basis": doc.amount_basis or "",
        "group_count": doc.group_count, "valid_count": doc.valid_count, "total_rows": doc.total_rows,
        "loaded_count": state["loaded_count"], "failed_count": doc.failed_count, "error": doc.error,
        "report": state["report"],
        # Loading, but no job queued or running: the worker stopped.
        "stalled": doc.status == "Loading" and not _job_running(doc.name),
        "refused": refused,
        # "problems": skipping the refused groups would load the rest; any
        # other refusal cannot be helped by skipping.
        "refused_kind": refused_kind if refused else None,
    }


# ── endpoints ──────────────────────────────────────────────────────────────

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


@frappe.whitelist(methods=["POST"])
def load(name, skip_invalid=0, amount_basis=None):
    """Re-check, then queue the load of every ready slice after the commit.

    Also resumes an upload whose load stopped or finished partly: a reload
    replaces, so nothing is loaded twice. Problems found by the re-check come
    back as `refused` with the fresh report (saved), unless `skip_invalid`
    says to load only the ready slices. A refused resume keeps the upload's
    earlier outcome.
    """
    _require_loader()
    doc = frappe.get_doc(DOCTYPE, name)
    doc.check_permission("write")
    if amount_basis:
        doc.amount_basis = amount_basis
    resumable = doc.status in ("Partly Loaded", "Failed") or (doc.status == "Loading" and not _job_running(doc.name))
    if doc.status != "Checked" and not resumable:
        frappe.throw("This upload is still loading." if doc.status == "Loading"
                     else f"This upload is {doc.status.lower()}; there is nothing left to load.")
    was = (doc.status, doc.failed_count, doc.error, doc.report, doc.group_count, doc.valid_count, doc.total_rows)

    def refuse(reason, refused_kind="other"):
        if resumable:
            (doc.status, doc.failed_count, doc.error, doc.report,
             doc.group_count, doc.valid_count, doc.total_rows) = was
            doc.save()
        return _payload(doc, refused=reason, refused_kind=refused_kind)

    _record_check(doc)
    if doc.status == "Failed":
        return refuse(doc.error or "The file could not be read.")
    report = json.loads(doc.report or "[]")
    ready = sum(1 for r in report if r["ok"])
    problems = len(report) - ready
    if not ready:
        return refuse("Nothing in this file is ready to load.")
    if problems and not cint(skip_invalid):
        return refuse(f"{problems} of {len(report)} groups have problems. "
                      f"Load only the {ready} that {'is' if ready == 1 else 'are'} ready, or fix the file.",
                      refused_kind="problems")
    doc.status = "Loading"
    doc.save()
    # `upload` is the only job kwarg: enqueue's own parameters cannot be.
    frappe.enqueue("konsol.budget_bulk.run_load", queue="long", timeout=JOB_TIMEOUT, job_id=_job_id(doc.name),
                   deduplicate=True, enqueue_after_commit=True, upload=doc.name)
    return _payload(doc)


@frappe.whitelist(methods=["GET"])
def get_upload(name):
    doc = frappe.get_doc(DOCTYPE, name)
    doc.check_permission("read")
    return _payload(doc)


# ── the load job ───────────────────────────────────────────────────────────

def _save_progress(name, report, failed, status=None, error=None):
    """The job's progress. The loaded count is not stored (upload_state)."""
    values = {"report": json.dumps(report), "failed_count": failed,
              "group_count": len(report), "valid_count": sum(1 for r in report if r["ok"]),
              "total_rows": sum(r["rows"] for r in report)}
    if status:
        values["status"] = status
    if error is not None:
        values["error"] = error
    frappe.db.set_value(DOCTYPE, name, values, update_modified=bool(status))
    frappe.db.commit()


def _as_dict(line):
    return line.as_dict() if hasattr(line, "as_dict") else dict(line)


def _assert_may_replace(entry):
    """The user may replace an existing entry only if they may write it and
    every line it holds: a slice is replaced whole, so a line on an account
    or dimension value the user cannot write would otherwise be dropped."""
    from konsol.api import _assert_budget_write_access

    entry.check_permission("write")
    for line in entry.get("lines") or []:
        values = _as_dict(line)
        row = {k: v for k, v in values.items() if k == "main_account" or k.startswith("dim_")}
        try:
            _assert_budget_write_access(dict(row, data_area_id=entry.data_area_id))
        except frappe.PermissionError as e:
            frappe.throw(f"Budget Entry {entry.name} (period {entry.fiscal_period}) holds account "
                         f"{values.get('main_account')}, which you cannot write, so it cannot be replaced: {e}",
                         frappe.PermissionError)


def _line(row):
    return {"main_account": row["main_account"], "debit": row.get("debit") or 0.0,
            "credit": row.get("credit") or 0.0, "description": row.get("description") or "",
            **{k: row.get(k) or "" for k in row if k.startswith("dim_")}}


def _replace_slice(upload, members):
    """Replace one (cycle, entity, layer) slice with the file's periods.
    Returns the entry names in `members` order. Every existing entry is
    judged before anything changes; the caller commits or rolls back."""
    from konsol.epm.doctype.budget_entry.budget_entry import judged_by_writer

    first = members[0][0]
    cycle, entity, layer = first["cycle"], first["entity"], first["layer"]
    existing = {int(r.fiscal_period): r.name for r in frappe.get_all(
        ENTRY, filters={"cycle": cycle, "data_area_id": entity, "layer": layer},
        fields=["name", "fiscal_period"], order_by="fiscal_period asc, name asc", limit_page_length=0)}
    docs = {p: frappe.get_doc(ENTRY, n) for p, n in existing.items()}
    for d in docs.values():
        _assert_may_replace(d)
    in_file = {int(item["fiscal_period"]) for item, _ in members}
    for p in sorted(set(existing) - in_file):
        # The write check above is the user's right to replace the slice;
        # Budget Entry grants delete to System Manager only.
        frappe.delete_doc(ENTRY, existing[p], ignore_permissions=True)
    names = []
    for item, rows in members:
        period = int(item["fiscal_period"])
        doc = docs.get(period) or frappe.get_doc({"doctype": ENTRY, "cycle": cycle, "data_area_id": entity,
                                                  "layer": layer, "fiscal_period": period})
        doc.currency = item["currency"]
        doc.amount_basis = item["amount_basis"]
        doc.source = "Upload"
        doc.budget_upload = upload
        doc.set("lines", [])
        for row in rows:
            doc.append("lines", _line(row))
        # run_load judged the file at the start of the job: the entry's
        # validate checks its Cycle but does not judge these rows again.
        with judged_by_writer():
            if period in docs:
                doc.save()
            else:
                doc.insert()
        names.append(doc.name)
    return names


def _outcome(loaded, failed, ready):
    if ready and loaded == ready and not failed:
        return "Loaded"
    return "Partly Loaded" if loaded else "Failed"


def run_load(upload):
    """Background job: replace every ready slice of the upload's file.

    Runs as the user who asked (Frappe sets the job's user), so the plan's
    access and writer-role checks apply to every row. The file is judged
    again here, against the facts as they are now. Each slice commits on its
    own and progress is saved after each; a failed slice is rolled back,
    recorded on its rows, and the rest carry on; a deadlock is retried once.
    """
    from rq.timeouts import BaseTimeoutException

    started = time.monotonic()
    doc = frappe.get_doc(DOCTYPE, upload)
    report = json.loads(doc.report or "[]")
    loaded = failed = 0
    try:
        result = budget_intake.plan(_read_table(doc.upload_file), form_basis=doc.amount_basis or "",
                                    name_lines=True)
        report = _whole_slices(result["report"])
        slices = {}
        # plan builds its report in the groups' order, one item per group.
        for (_key, rows), item in zip(result["groups"].items(), report):
            slices.setdefault(_slice(item), []).append((item, rows))
        ready = [members for members in slices.values() if all(item["ok"] for item, _ in members)]
        total = sum(len(members) for members in ready)
        stopped = False
        for members in ready:
            if time.monotonic() - started > TIME_BUDGET_SECONDS:
                stopped = True
                break
            for attempt in (1, 2):
                try:
                    names = _replace_slice(doc.name, members)
                    frappe.db.commit()
                    for (item, _), name in zip(members, names):
                        item["loaded"] = name
                        item.pop("load_error", None)
                    loaded += len(members)
                    break
                except BaseTimeoutException:
                    raise
                except Exception as e:
                    frappe.db.rollback()
                    frappe.clear_messages()
                    if attempt == 1 and (isinstance(e, frappe.QueryDeadlockError) or frappe.db.is_deadlocked(e)):
                        continue
                    reason = strip_html(str(e)) or type(e).__name__
                    for item, _ in members:
                        item["load_error"] = reason
                    failed += len(members)
                    break
            _save_progress(doc.name, report, failed)
        if stopped:
            _save_progress(doc.name, report, failed, status="Partly Loaded" if loaded else "Failed",
                           error="Stopped at the time limit; load again to finish it.")
        else:
            _save_progress(doc.name, report, failed, status=_outcome(loaded, failed, total),
                           error="" if total else "Nothing in this file was ready to load.")
    except Exception as e:
        frappe.db.rollback()
        if not isinstance(e, BaseTimeoutException):
            frappe.log_error(title=f"Budget upload {doc.name} failed")
        reason = ("The load ran out of time; load again to finish it." if isinstance(e, BaseTimeoutException)
                  else strip_html(str(e)) or type(e).__name__)
        _save_progress(doc.name, report, failed, status="Partly Loaded" if loaded else "Failed", error=reason)

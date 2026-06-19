"""Close Assertions — per-close green/red reconciliation board (PRD §6.10 §5).

For a given (or the latest) Close Run, lists each assertion grouped by category
with pass/fail, the failing-row count, and where the offending rows live — plus
a category chart and a sign-off-aware summary.
"""
import frappe

from konsol.consolidation.doctype.close_run.close_run import (
    SIGNED_STATES, TERMINAL_STATUSES)

STATUS_ORDER = {"Error": 0, "Fail": 1, "Pass": 2}  # surface problems first
_RUN_FIELDS = ["name", "status", "signoff_status", "total", "passed", "failed",
               "errored", "fiscal_year", "fiscal_period", "signed_off_by"]


def execute(filters=None):
    filters = frappe._dict(filters or {})
    run = _resolve_run(filters)
    if not run:
        return _columns(), [], _no_run_message(), None, []

    rows = frappe.get_all(
        "Assertion Result",
        filters={"parent": run.name},
        fields=["dimension as category", "assertion", "status",
                "rows_failed", "message", "failures_table"],
        limit_page_length=0,
    )
    rows.sort(key=lambda r: (r.category or "", STATUS_ORDER.get(r.status, 9), r.assertion or ""))

    return _columns(), rows, _message(run), _chart(rows), _summary(run)


def _resolve_run(filters):
    """The chosen run as a dict, or None. One query in each branch."""
    if filters.get("close_run"):
        return frappe.db.get_value("Close Run", filters.close_run, _RUN_FIELDS, as_dict=True)
    period = {"status": ["in", TERMINAL_STATUSES]}
    if filters.get("fiscal_year"):
        period["fiscal_year"] = filters.fiscal_year
    if filters.get("fiscal_period"):
        period["fiscal_period"] = filters.fiscal_period
    rows = frappe.get_all("Close Run", filters=period, fields=_RUN_FIELDS,
                          order_by="completed_at desc, creation desc", limit=1)
    return rows[0] if rows else None


def _columns():
    return [
        {"label": frappe._("Category"), "fieldname": "category", "fieldtype": "Data", "width": 150},
        {"label": frappe._("Assertion"), "fieldname": "assertion", "fieldtype": "Data", "width": 320},
        {"label": frappe._("Result"), "fieldname": "status", "fieldtype": "Data", "width": 90},
        {"label": frappe._("Failing Rows"), "fieldname": "rows_failed", "fieldtype": "Int", "width": 100},
        {"label": frappe._("Detail / Reason"), "fieldname": "message", "fieldtype": "Data", "width": 380},
        {"label": frappe._("Failures Relation"), "fieldname": "failures_table", "fieldtype": "Data", "width": 260},
    ]


def _chart(rows):
    cats, passed, failed = [], {}, {}
    for r in rows:
        c = r.category or "Other"
        if c not in passed:
            cats.append(c)
            passed[c] = 0
            failed[c] = 0
        if r.status == "Pass":
            passed[c] += 1
        else:
            failed[c] += 1
    return {
        "data": {
            "labels": cats,
            "datasets": [
                {"name": frappe._("Passed"), "values": [passed[c] for c in cats]},
                {"name": frappe._("Failed/Errored"), "values": [failed[c] for c in cats]},
            ],
        },
        "type": "bar",
        "barOptions": {"stacked": True},
        "colors": ["green", "red"],
    }


def _summary(run):
    green = run.status == "Green"
    signed = run.signoff_status in SIGNED_STATES
    out = [
        {"label": frappe._("Status"), "value": run.status,
         "indicator": "Green" if green else "Red"},
        {"label": frappe._("Sign-off"), "value": run.signoff_status or frappe._("Not Signed Off"),
         "indicator": "Green" if signed else "Orange"},
        {"label": frappe._("Passed"), "value": run.passed or 0, "indicator": "Green"},
        {"label": frappe._("Failed"), "value": run.failed or 0,
         "indicator": "Red" if run.failed else "Green"},
        {"label": frappe._("Errored"), "value": run.errored or 0,
         "indicator": "Red" if run.errored else "Green"},
    ]
    if run.signed_off_by:
        out.append({"label": frappe._("Signed off by"), "value": run.signed_off_by,
                    "indicator": "Blue"})
    return out


def _message(run):
    period = (f"{run.fiscal_year}-P{run.fiscal_period}" if run.fiscal_year
              else frappe._("(unscoped)"))
    return frappe._("Close Run {0} · period {1} · status {2} · sign-off {3}").format(
        run.name, period, run.status, run.signoff_status or frappe._("Not Signed Off"))


def _no_run_message():
    return frappe._("No completed Close Run found. Trigger the assertion suite from a "
                    "Close Run, then reopen this report.")

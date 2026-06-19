"""Close Assertions — per-close green/red reconciliation board (PRD §6.10 §5).

For a given (or the latest) Close Run, lists each assertion grouped by category
with pass/fail, the failing-row count, and where the offending rows live — plus
a category chart and a sign-off-aware summary.
"""
import frappe

STATUS_ORDER = {"Error": 0, "Fail": 1, "Pass": 2}  # surface problems first


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
    if filters.get("close_run"):
        return frappe.db.get_value(
            "Close Run", filters.close_run,
            ["name", "status", "signoff_status", "total", "passed", "failed",
             "errored", "fiscal_year", "fiscal_period", "signed_off_by"], as_dict=True)
    # default: latest terminal run (optionally for a given period)
    period_filters = {"status": ["in", ("Green", "Red", "Error")]}
    if filters.get("fiscal_year"):
        period_filters["fiscal_year"] = filters.fiscal_year
    if filters.get("fiscal_period"):
        period_filters["fiscal_period"] = filters.fiscal_period
    latest = frappe.get_all("Close Run", filters=period_filters,
                            fields=["name"], order_by="creation desc", limit=1)
    if not latest:
        return None
    return frappe.db.get_value(
        "Close Run", latest[0].name,
        ["name", "status", "signoff_status", "total", "passed", "failed",
         "errored", "fiscal_year", "fiscal_period", "signed_off_by"], as_dict=True)


def _columns():
    return [
        {"label": "Category", "fieldname": "category", "fieldtype": "Data", "width": 150},
        {"label": "Assertion", "fieldname": "assertion", "fieldtype": "Data", "width": 320},
        {"label": "Result", "fieldname": "status", "fieldtype": "Data", "width": 90},
        {"label": "Failing Rows", "fieldname": "rows_failed", "fieldtype": "Int", "width": 100},
        {"label": "Detail / Reason", "fieldname": "message", "fieldtype": "Data", "width": 380},
        {"label": "Failures Relation", "fieldname": "failures_table", "fieldtype": "Data", "width": 260},
    ]


def _chart(rows):
    cats, passed, failed = [], {}, {}
    for r in rows:
        c = r.category or "Other"
        if c not in passed:
            cats.append(c); passed[c] = 0; failed[c] = 0
        if r.status == "Pass":
            passed[c] += 1
        else:
            failed[c] += 1
    return {
        "data": {
            "labels": cats,
            "datasets": [
                {"name": "Passed", "values": [passed[c] for c in cats]},
                {"name": "Failed/Errored", "values": [failed[c] for c in cats]},
            ],
        },
        "type": "bar",
        "barOptions": {"stacked": True},
        "colors": ["#28a745", "#dc3545"],
    }


def _summary(run):
    green = run.status == "Green"
    return [
        {"label": "Status", "value": run.status,
         "indicator": "Green" if green else "Red"},
        {"label": "Sign-off", "value": run.signoff_status or "Not Signed Off",
         "indicator": "Green" if run.signoff_status in ("Signed Off", "Overridden") else "Orange"},
        {"label": "Passed", "value": run.passed or 0, "indicator": "Green"},
        {"label": "Failed", "value": run.failed or 0,
         "indicator": "Red" if run.failed else "Green"},
        {"label": "Errored", "value": run.errored or 0,
         "indicator": "Red" if run.errored else "Green"},
    ]


def _message(run):
    period = f"{run.fiscal_year}-P{run.fiscal_period}" if run.fiscal_year else "(unscoped)"
    return (f"<b>Close Run {run.name}</b> &middot; period {period} &middot; "
            f"status <b>{run.status}</b> &middot; sign-off <b>{run.signoff_status or 'Not Signed Off'}</b>")


def _no_run_message():
    return ("No completed Close Run found. Trigger the assertion suite from a "
            "Close Run, then reopen this report.")

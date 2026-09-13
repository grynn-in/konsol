"""Loading the group chart from one file (konsol#182): check, load, publish.

Not Data Import, for four reasons:

1. Data Import sets frappe.flags.in_import, and the write-through skips its
   sync under that flag: Published rows would not reach ClickHouse until the
   next migrate reconciled them.
2. A tree needs every parent before its children; Data Import goes in file
   order and fails a child listed before its parent.
3. The cross-row rules (a parent is a heading in the same chart, no circles,
   one chart per file) need the whole file, reported in one pass.
4. One governed rebuild for the whole chart, not one per row.

Flow (the Main Account list's "Upload chart" and "Publish chart"):

  1. ``check_chart_file`` reads the uploaded file (CSV, or the first sheet of
     an .xlsx) and reports what a load would do. Nothing is written.
  2. ``load_chart`` checks again and, only if nothing is wrong, writes it all in
     one transaction: a new code becomes a Draft; an existing row is updated
     and keeps its status (a Published one stays Published, and its changes
     are listed). A code missing from the file is listed, never deleted.
  3. ``publish_chart`` publishes every Draft of one chart, parents first, and
     requests one governed ``chart`` rebuild.

The ``chart`` build is ``@silver_main_accounts``: the chart, everything it
classifies, and everything those read, so it also runs on a site whose
warehouse has never built (review of #183).

Only the Close Lead (EPM Admin) may do any of it. This is how a site gets its
chart (decided 13 Sep 2026: konsol defines the shape; there is no ERP
adoption), besides entering it in Desk.
"""
import frappe

from konsol import group_chart_model as M
from konsol.schema_lifecycle import check_epm_admin, request_governed_rebuild

DOCTYPE = "Main Account"
BUILD_SCOPE = "chart"
def _require_chart_admin():
    check_epm_admin()
    if not frappe.has_permission(DOCTYPE, "create"):
        frappe.throw("Loading the group chart needs the Close Lead role (EPM Admin).", frappe.PermissionError)


def _existing():
    """{code: row} of every Main Account, whatever its chart or status."""
    rows = frappe.get_all(DOCTYPE, fields=["name", "status", *M.DECLARED_FIELDS], limit_page_length=0)
    return {r["name"]: {**r, "main_account": r["name"]} for r in rows}


def _check(file_url):
    from konsol.tb_bulk import _read_table   # the same reader, permission check included

    try:
        rows = M.parse_chart_table(_read_table(file_url))
    except ValueError as e:
        return {"ok": False, "errors": str(e).splitlines(), "writes": [], "chart_of_accounts": "", "rows": 0,
                "insert": [], "update": [], "published_changes": [], "inactive": [], "unchanged": [],
                "not_ready": [], "not_in_file": []}
    return M.plan_chart_load(rows, _existing())


def _public(report, file_url):
    out = {k: v for k, v in report.items() if k != "writes"}
    out["file_url"] = file_url
    out["file_name"] = (file_url or "").rsplit("/", 1)[-1]
    return out


@frappe.whitelist(methods=["POST"])
def check_chart_file(file_url):
    """Report what loading this file would do. Writes nothing."""
    _require_chart_admin()
    return _public(_check(file_url), file_url)


@frappe.whitelist(methods=["POST"])
def load_chart(file_url):
    """Load the file, all or nothing. Re-checked first: any problem, and
    nothing is written (the report comes back with ``loaded`` False)."""
    _require_chart_admin()
    report = _check(file_url)
    out = _public(report, file_url)
    if not report["ok"]:
        return {**out, "loaded": False}
    changed_live = None
    try:
        for action, code, fields in report["writes"]:
            if action == "insert":
                frappe.get_doc({"doctype": DOCTYPE, "main_account": code, **fields, "status": "Draft",
                                "source": "Upload", "source_note": out["file_name"]}).insert()
                continue
            doc = frappe.get_doc(DOCTYPE, code)
            doc.update(fields)
            doc.save()
            if doc.status == M.PUBLISHED:
                changed_live = changed_live or doc
        if changed_live:
            # a Published account changed what the warehouse classifies: one rebuild
            out["build"] = request_governed_rebuild(changed_live, "Chart upload", scope=BUILD_SCOPE)
    except Exception:
        frappe.db.rollback()
        raise
    return {**out, "loaded": True}


@frappe.whitelist(methods=["POST"])
def publish_chart(chart_of_accounts):
    """Publish every Draft of one chart, parents first (by lft), in one
    transaction, with one governed rebuild. Every Draft is checked before any
    is published: one that is not ready refuses them all, naming each."""
    _require_chart_admin()
    existing = _existing()
    names = frappe.get_all(DOCTYPE, filters={"chart_of_accounts": chart_of_accounts, "status": "Draft"},
                           order_by="lft asc", pluck="name", limit_page_length=0)
    if not names:
        return {"published": [], "chart_of_accounts": chart_of_accounts}
    batch = set(names)
    problems = []
    for name in names:
        row = M.apply_defaults(existing[name])
        parent_code = M.text(row.get("parent_account"))
        parent = existing.get(parent_code)
        if parent and parent_code in batch:
            parent = {**parent, "status": M.PUBLISHED}   # published before it, in this batch
        parent = M.apply_defaults(parent) if parent else None
        problems += M.declaration_problems(row, parent) + M.publish_problems(row, parent)
    if problems:
        frappe.throw("Nothing was published:\n" + "\n".join(problems[:50])
                     + (f"\n(and {len(problems) - 50} more)" if len(problems) > 50 else ""))
    first = None
    try:
        for name in names:
            doc = frappe.get_doc(DOCTYPE, name)
            doc.status = M.PUBLISHED
            doc.save()
            first = first or doc
        build = request_governed_rebuild(first, "Publish chart", scope=BUILD_SCOPE)
    except Exception:
        frappe.db.rollback()
        raise
    return {"published": names, "chart_of_accounts": chart_of_accounts, "build": build}

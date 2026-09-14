"""Declare an EPM Fiscal Year for every fiscal year in use (konsol#189).

Until now a (fiscal_year, fiscal_period) pair was implied: documents carry it
and Period Status rows hold its status. This patch collects every pair in use,
asks fiscal_migration_model.plan() what to declare, and writes it: one
Monthly (12) calendar year per fiscal year not yet declared, with the Period
Status statuses carried onto the matching period rows.

patches.txt has no sections, so this runs pre_model_sync: it reloads the two
new doctypes (child first) before any query. Any conflict stops the patch
before it writes anything. A second run plans nothing.
"""
import importlib.util
import os

import frappe

_APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load(name):
    """A konsol module loaded by path, as the fiscal models load siblings."""
    spec = importlib.util.spec_from_file_location(
        "_create_fiscal_years_" + name, os.path.join(_APP, name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _has_period_columns(doctype):
    # table_exists first: has_column raises on a missing table.
    return (frappe.db.table_exists(doctype)
            and frappe.db.has_column(doctype, "fiscal_year")
            and frappe.db.has_column(doctype, "fiscal_period"))


def _year(value, where, bad):
    """``value`` as an int year, or None (blank). A non-numeric year is
    recorded in ``bad``: no Fiscal Year can be declared for it."""
    if value is None or str(value).strip() == "":
        return None
    try:
        return int(str(value).strip())
    except ValueError:
        bad.append("%s uses fiscal year %r, which is not a year number." % (where, value))
        return None


def _used_pairs(calendar, bad):
    pairs = set()
    submittable = getattr(calendar, "_PERIOD_DATA", {})
    for doctype in calendar.DOCTYPES_USING_PERIODS:
        if not _has_period_columns(doctype):
            continue
        where = " WHERE docstatus < 2" if submittable.get(doctype, True) else ""
        rows = frappe.db.sql(
            f"SELECT DISTINCT fiscal_year, fiscal_period FROM `tab{doctype}`{where}")
        for fiscal_year, fiscal_period in rows:
            year = _year(fiscal_year, doctype, bad)
            if year is not None and fiscal_period is not None:
                pairs.add((year, int(fiscal_period)))
    return pairs


def _period_status_rows(bad):
    if not _has_period_columns("Period Status"):
        return []
    rows = frappe.db.sql(
        "SELECT fiscal_year, fiscal_period, status, start_date, end_date, "
        "closed_by, closed_on FROM `tabPeriod Status`", as_dict=True)
    out = []
    for row in rows:
        year = _year(row["fiscal_year"], "Period Status", bad)
        if year is None or row["fiscal_period"] is None:
            continue
        out.append({
            "fiscal_year": year,
            "fiscal_period": int(row["fiscal_period"]),
            "status": row["status"],
            "start_date": row["start_date"],
            "end_date": row["end_date"],
            "closed_by": row["closed_by"],
            "closed_on": row["closed_on"],
        })
    return out


def _existing_years():
    """year -> {"name", "rows"} for EPM Fiscal Years already declared."""
    years = {}
    names = {}
    for row in frappe.db.sql(
            "SELECT name, fiscal_year FROM `tabEPM Fiscal Year`", as_dict=True):
        years[int(row["fiscal_year"])] = {"name": row["name"], "rows": []}
        names[row["name"]] = int(row["fiscal_year"])
    for row in frappe.db.sql(
            "SELECT parent, fiscal_period, period_code, start_date, end_date, status "
            "FROM `tabEPM Fiscal Year Period` WHERE parenttype = 'EPM Fiscal Year'",
            as_dict=True):
        year = names.get(row["parent"])
        if year is None:
            continue
        years[year]["rows"].append({
            "period": row["fiscal_period"],
            "code": row["period_code"],
            "start_date": row["start_date"],
            "end_date": row["end_date"],
            "status": row["status"],
        })
    return years


def _period_row(row, move):
    out = {
        "fiscal_period": row["period"],
        "period_code": row["code"],
        "period_label": row["label"],
        "period_type": row["type"],
        "quarter": row.get("quarter") or "",
        "start_date": row["start_date"],
        "end_date": row["end_date"],
        "status": row["status"],
    }
    if move:
        out.update(status=move["status"], closed_by=move["closed_by"],
                   closed_on=move["closed_on"])
    return out


def execute():
    # Child first, then parent, before anything reads either table.
    frappe.reload_doc("epm", "doctype", "epm_fiscal_year_period")
    frappe.reload_doc("epm", "doctype", "epm_fiscal_year")

    calendar = _load("fiscal_calendar")
    model = _load("fiscal_migration_model")

    bad = []
    used = _used_pairs(calendar, bad)
    ps_rows = _period_status_rows(bad)
    existing = _existing_years()

    result = model.plan(used, ps_rows, existing)
    conflicts = bad + result["conflicts"]
    if conflicts:
        frappe.throw(
            "Cannot declare Fiscal Years; nothing was written. Resolve these "
            "%d conflict(s) and migrate again:\n%s"
            % (len(conflicts), "\n".join("- " + c for c in conflicts)),
            title="Fiscal Year migration")

    moves = {}
    for move in result["moves"]:
        moves.setdefault(move["year"], {})[move["code"]] = move

    for year in result["create"]:
        year_moves = moves.pop(year["year"], {})
        doc = frappe.get_doc({
            "doctype": "EPM Fiscal Year",
            "fiscal_year": year["year"],
            "start_date": year["start_date"],
            "end_date": year["end_date"],
            "period_pattern": year["period_pattern"],
            "include_opening_period": 1,
            "include_closing_period": 1,
            "periods": [_period_row(r, year_moves.get(r["code"])) for r in year["rows"]],
        })
        doc.flags.konsol_fiscal_migration = True
        doc.insert(ignore_permissions=True)

    for year, year_moves in sorted(moves.items()):
        doc = frappe.get_doc("EPM Fiscal Year", existing[year]["name"])
        for row in doc.periods:
            move = year_moves.get(row.period_code)
            if move:
                row.status = move["status"]
                row.closed_by = move["closed_by"]
                row.closed_on = move["closed_on"]
        doc.flags.konsol_fiscal_migration = True
        doc.save(ignore_permissions=True)

    print("create_fiscal_years: %d fiscal year(s) created, %d period status(es) moved"
          % (len(result["create"]), len(result["moves"])))

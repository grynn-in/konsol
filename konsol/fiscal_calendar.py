"""Which documents use which fiscal periods (konsol#189).

A declared period that any document uses is frozen by the fiscal-structure
rules (``fiscal_structure_model.used_period_problems``). This module says which
doctypes hold period data and finds the periods of a year they use.

Every doctype with ``fiscal_year`` and an Int ``fiscal_period`` must appear in
exactly one of ``DOCTYPES_USING_PERIODS`` or ``NOT_PERIOD_DATA``;
``konsol/tests/test_fiscal_calendar.py`` fails on a new one left out.

frappe is imported at the top only for ``@frappe.whitelist``; every function
imports it again when called, so the frappe installed at call time is the
one used (the host tests install a stub frappe around each call).
"""
import frappe

#: Doctype -> whether it is submittable. Submittable doctypes skip cancelled
#: documents (docstatus 2); drafts count. Non-submittable ones count every row.
_PERIOD_DATA = {
    "Trial Balance Submission": True,
    "Consolidation Adjustment": True,
    "IC Balance": True,
    "Group Exchange Rate": True,
    "Allocation Run": True,
    "Allocation Driver": False,
    "Assertion Run": False,
}

#: Doctypes whose documents carry a fiscal year + period that must be declared.
DOCTYPES_USING_PERIODS = tuple(_PERIOD_DATA)

#: Doctypes with fiscal_year + fiscal_period fields that are not period data.
NOT_PERIOD_DATA = {
    "Period Status": "the period calendar itself: it declares open/closed periods, it does not use them",
    "Pipeline Run": "a build log: the period is a run parameter, the data it builds lives in the warehouse",
}


def periods_in_use(fiscal_year, lock=False):
    """The set of period numbers any period-data document uses in ``fiscal_year``.

    ``lock=True`` is for a caller that gates a write on the answer (the
    EPM Fiscal Year freeze): every SELECT becomes a locking read (LOCK IN
    SHARE MODE). Under REPEATABLE READ a plain SELECT returns the
    transaction's snapshot and misses a document committed after it; a
    locking read returns the latest committed rows (PR #191 review 6).
    The default stays a plain read."""
    import frappe

    selects = []
    for doctype, submittable in _PERIOD_DATA.items():
        where = "fiscal_year = %(fiscal_year)s"
        if submittable:
            where += " AND docstatus < 2"
        select = f"SELECT DISTINCT fiscal_period FROM `tab{doctype}` WHERE {where}"
        # MariaDB takes a locking clause inside a UNION only on a
        # parenthesised SELECT.
        selects.append(f"({select} LOCK IN SHARE MODE)" if lock else select)
    rows = frappe.db.sql("\nUNION\n".join(selects), {"fiscal_year": fiscal_year})
    return {int(row[0]) for row in rows if row[0] is not None}


def current_period(rows, today):
    """(fiscal_year, fiscal_period) of the declared Regular row in ``rows``
    whose ``start_date``..``end_date`` contains ``today``, or ``None`` when
    none does. ``rows`` is any iterable of dict-likes exposing ``fiscal_year``,
    ``fiscal_period``, ``period_type``, ``start_date`` and ``end_date`` — what
    ``fiscal_period_rows()`` returns, or the equivalent a caller already read.

    Shared by ``home_api.period_tree`` and ``control_api``'s snapshot so the
    two can't drift (konsol#189 review nit 6): never a guess from today's
    calendar month/year, since a fiscal year need not run Jan-Dec."""
    import frappe

    getdate = frappe.utils.getdate
    for row in rows:
        if row.get("period_type") != "Regular":
            continue
        start, end = row.get("start_date"), row.get("end_date")
        if start and end and getdate(start) <= today <= getdate(end):
            return (row.get("fiscal_year"), int(row.get("fiscal_period")))
    return None


def _load_sibling(name):
    """Load a sibling konsol module by path (as fiscal_migration_model does),
    so this module keeps loading without frappe or a package-relative import."""
    import importlib.util
    import os

    here = os.path.dirname(os.path.abspath(__file__))
    spec = importlib.util.spec_from_file_location(
        "_fiscal_calendar_" + name, os.path.join(here, name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def fiscal_period_rows():
    """The rows konsol publishes to epm_staging.fiscal_periods (konsol#189).

    One row per EPM Fiscal Year Period, joined to its parent EPM Fiscal Year
    in a single query. Keys are exactly the ClickHouse DDL columns for
    epm_staging.fiscal_periods, in order (clickhouse.py's
    _REFERENCE_TABLE_DDL) — a blank period_label falls back to its code, a
    blank quarter becomes '', and status is the EFFECTIVE status: the
    stricter of the year's status and the row's own (fiscal_status_model).
    """
    import frappe

    status_model = _load_sibling("fiscal_status_model")

    joined = frappe.db.sql(
        """
        SELECT
            y.fiscal_year AS fiscal_year,
            p.fiscal_period AS fiscal_period,
            p.period_code AS period_code,
            p.period_label AS period_label,
            p.period_type AS period_type,
            p.start_date AS start_date,
            p.end_date AS end_date,
            p.quarter AS quarter,
            y.status AS year_status,
            p.status AS row_status
        FROM `tabEPM Fiscal Year` y
        JOIN `tabEPM Fiscal Year Period` p
            ON p.parent = y.name
            AND p.parenttype = 'EPM Fiscal Year'
            AND p.parentfield = 'periods'
        ORDER BY y.fiscal_year, p.fiscal_period
        """,
        as_dict=True,
    )

    rows = []
    for row in joined:
        rows.append({
            "fiscal_year": row["fiscal_year"],
            "fiscal_period": row["fiscal_period"],
            "period_code": row["period_code"],
            "period_label": row["period_label"] or row["period_code"],
            "period_type": row["period_type"],
            "start_date": row["start_date"],
            "end_date": row["end_date"],
            "quarter": row["quarter"] or "",
            "status": status_model.effective_status(row["year_status"], row["row_status"]),
        })
    return rows


# ---- declaring Fiscal Years for the periods in use (konsol#189) -------------
# Shared by patches/create_fiscal_years.py and declare_years_in_use: collect
# every (fiscal_year, fiscal_period) in use, ask fiscal_migration_model.plan()
# what to declare, and apply that plan.

#: The warehouse read of declare_years_in_use(include_warehouse=1).
WAREHOUSE_TABLE = "epm_gold.gold_trial_balance"


def _has_period_columns(doctype):
    import frappe

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


def _add_pairs(pairs, rows, where, bad):
    for fiscal_year, fiscal_period in rows:
        year = _year(fiscal_year, where, bad)
        if year is not None and fiscal_period is not None:
            pairs.add((year, int(fiscal_period)))


def used_period_pairs(bad):
    """Every (year, period) a period-data document uses (MariaDB).
    Cancelled documents of submittable doctypes do not count."""
    import frappe

    pairs = set()
    for doctype, submittable in _PERIOD_DATA.items():
        if not _has_period_columns(doctype):
            continue
        where = " WHERE docstatus < 2" if submittable else ""
        rows = frappe.db.sql(
            f"SELECT DISTINCT fiscal_year, fiscal_period FROM `tab{doctype}`{where}")
        _add_pairs(pairs, rows, doctype, bad)
    return pairs


def warehouse_period_pairs(bad):
    """Every (year, period) in the warehouse trial balance (ClickHouse)."""
    import json

    from konsol.clickhouse import execute

    raw = execute(f"SELECT DISTINCT fiscal_year, fiscal_period FROM {WAREHOUSE_TABLE} FORMAT JSONCompact")
    pairs = set()
    _add_pairs(pairs, json.loads(raw).get("data", []) if raw else [], WAREHOUSE_TABLE, bad)
    return pairs


def period_status_rows(bad):
    """The migration planner's own reader of the retiring doctype: it is what
    lets create_fiscal_years / declare_years_in_use carry old Period Status
    statuses onto the matching declared rows. Stops reading it only when
    Period Status is actually dropped (plan 3.4 PR4, task 90)."""
    import frappe

    # TODO konsol#189: drops with PR4 (task 90), when Period Status is retired.
    if not _has_period_columns("Period Status"):
        return []
    rows = frappe.db.sql(
        "SELECT fiscal_year, fiscal_period, status, start_date, end_date, "
        "closed_by, closed_on FROM `tabPeriod Status`", as_dict=True)
    out = []
    for row in rows:
        # TODO konsol#189: drops with PR4 (task 90), when Period Status is retired.
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


def existing_fiscal_years():
    """year -> {"name", "rows"} for EPM Fiscal Years already declared."""
    import frappe

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


def plan_fiscal_years(include_warehouse=False):
    """Collect the periods in use and plan what to declare. Writes nothing.

    Returns (plan, conflicts, existing): plan is fiscal_migration_model.plan()'s
    {create, moves, conflicts}; conflicts adds the unreadable years to it;
    existing is what apply_fiscal_year_plan() needs to find years to update."""
    model = _load_sibling("fiscal_migration_model")
    bad = []
    used = used_period_pairs(bad)
    if include_warehouse:
        used |= warehouse_period_pairs(bad)
    ps_rows = period_status_rows(bad)
    existing = existing_fiscal_years()
    result = model.plan(used, ps_rows, existing)
    return result, bad + result["conflicts"], existing


def throw_conflicts(conflicts, retry):
    """frappe.throw listing every conflict, if there are any."""
    import frappe

    if conflicts:
        frappe.throw(
            "Cannot declare Fiscal Years; nothing was written. Resolve these "
            "%d conflict(s) and %s:\n%s"
            % (len(conflicts), retry, "\n".join("- " + c for c in conflicts)),
            title="Fiscal Year migration")


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


def apply_fiscal_year_plan(result, existing):
    """Insert the planned years and carry the status moves across. Call only
    once throw_conflicts() has passed."""
    import frappe

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


def _flag(value, default):
    """A request flag: True/False/1/0 or "1"/"0"/"true"/"false"; blank is ``default``."""
    import frappe

    if value is None or value == "":
        return default
    return bool(frappe.utils.sbool(value))


@frappe.whitelist(methods=["POST"])
def declare_years_in_use(include_warehouse=False, dry_run=True):
    """Declare an EPM Fiscal Year for every fiscal year in use: the one-off
    tool for stacks whose warehouse holds years no konsol document names.
    System Manager only.

    Collects the periods in use as patches/create_fiscal_years does (MariaDB),
    plus the warehouse trial balance's when ``include_warehouse`` is set. A
    dry run (the default) writes nothing and lists any conflicts; a real run
    throws on any conflict before writing, else creates the years as the
    patch does. Returns {dry_run, create: [{fiscal_year, periods}], moves:
    [{fiscal_year, period_code, status}], conflicts}."""
    import frappe

    frappe.only_for("System Manager")
    include_warehouse = _flag(include_warehouse, False)
    dry_run = _flag(dry_run, True)

    result, conflicts, existing = plan_fiscal_years(include_warehouse=include_warehouse)
    summary = {
        "dry_run": dry_run,
        "create": [{"fiscal_year": y["year"], "periods": len(y["rows"])} for y in result["create"]],
        "moves": [{"fiscal_year": m["year"], "period_code": m["code"], "status": m["status"]}
                  for m in result["moves"]],
        "conflicts": list(conflicts),
    }
    if dry_run:
        return summary
    throw_conflicts(conflicts, retry="run it again")
    apply_fiscal_year_plan(result, existing)
    return summary

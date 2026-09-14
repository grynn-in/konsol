"""Which documents use which fiscal periods (konsol#189).

A declared period that any document uses is frozen by the fiscal-structure
rules (``fiscal_structure_model.used_period_problems``). This module says which
doctypes hold period data and finds the periods of a year they use.

Every doctype with ``fiscal_year`` and an Int ``fiscal_period`` must appear in
exactly one of ``DOCTYPES_USING_PERIODS`` or ``NOT_PERIOD_DATA``;
``konsol/tests/test_fiscal_calendar.py`` fails on a new one left out.

frappe is imported inside functions only, so the module loads without it.
"""

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


def periods_in_use(fiscal_year):
    """The set of period numbers any period-data document uses in ``fiscal_year``."""
    import frappe

    selects = []
    for doctype, submittable in _PERIOD_DATA.items():
        where = "fiscal_year = %(fiscal_year)s"
        if submittable:
            where += " AND docstatus < 2"
        selects.append(f"SELECT DISTINCT fiscal_period FROM `tab{doctype}` WHERE {where}")
    rows = frappe.db.sql("\nUNION\n".join(selects), {"fiscal_year": fiscal_year})
    return {int(row[0]) for row in rows if row[0] is not None}


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

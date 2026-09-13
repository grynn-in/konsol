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
}

#: Doctypes whose documents carry a fiscal year + period that must be declared.
DOCTYPES_USING_PERIODS = tuple(_PERIOD_DATA)

#: Doctypes with fiscal_year + fiscal_period fields that are not period data.
NOT_PERIOD_DATA = {
    "Period Status": "the period calendar itself: it declares open/closed periods, it does not use them",
    "Pipeline Run": "a build log: the period is a run parameter, the data it builds lives in the warehouse",
    "Assertion Run": "a close-check log: it records checks run against a period, not data posted to it",
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

"""Period strips endpoint: ``period_strip_info()`` (GET) returns the declared
fiscal years and the latest period with data (``period_strip_model.strip_info``)
so the Desk Year and Period strips can mark years and open on a period.

Two short warehouse reads, each on its own: the actuals' periods and years
(``gold_fully_consolidated_tb``). A read that fails (warehouse not built, any
error) contributes nothing: with no actuals the latest is None and the model
falls back to the latest declared Regular period. Never raises for a
warehouse failure.

The budget years (konsol#348 S5 K15; PR #554 review m7b) are the fiscal years
of the SUBMITTED Approved Budgets (docstatus 1), read from MariaDB: for the
root Consolidation Group document(s) of the given group's code when one is
given, else every group's. The warehouse's budget table holds every approved
Cycle's rows whatever any declaration says, so it is not read for this: the
mark says "an approved budget is declared for this year", so a year budgeted
before any actuals arrive is marked on the Year strip. ``data_years`` is the
union; ``latest`` (the period a first visit opens on) is the actuals' only,
because the Consolidated Statement refuses a period with no actuals rows. A
MariaDB failure is a site fault and is not swallowed.
"""
import frappe

from konsol import fiscal_calendar
from konsol.close import ch_read, period_strip_model
from konsol.consolidation.report.consolidated_statement.consolidated_statement import _group_code

#: Distinct periods with data, newest first. Non-Regular periods (P0, P13) are
#: filtered by the model against the declared calendar; LIMIT leaves room for them.
_PERIODS_SQL = (
    "SELECT DISTINCT fiscal_year, fiscal_period FROM epm_gold.gold_fully_consolidated_tb "
    "{where}ORDER BY fiscal_year DESC, fiscal_period DESC LIMIT 200"
)
_YEARS_SQL = (
    "SELECT DISTINCT fiscal_year FROM epm_gold.gold_fully_consolidated_tb "
    "{where}ORDER BY fiscal_year"
)
_GROUP_WHERE = "WHERE consolidation_group = {group:String} "

#: The three Desk reports that show the strips.
_REPORTS = ("Consolidation Worksheet", "Consolidated Statement", "Statement Drill")


def _may_open_a_period_report():
    """True when the user may open at least one of the three reports, by the
    check Frappe makes when it runs a query report (desk/query_report.py
    get_report_doc): the report is not disabled, ``Report.is_permitted()`` (Has Role / Custom Role) and
    the ``report`` right on the report's reference doctype."""
    for name in _REPORTS:
        try:
            doc = frappe.get_doc("Report", name)
        except frappe.DoesNotExistError:
            continue
        if not doc.disabled and doc.is_permitted() and frappe.has_permission(doc.ref_doctype, "report"):
            return True
    return False


def _approved_budget_years(group):
    """The fiscal years with a SUBMITTED Approved Budget (module docstring):
    ``group`` is the warehouse code (``_group_code``), or None for every
    group. An Approved Budget links the root Consolidation Group document,
    not the code, so the code's root row(s) are resolved first (the
    statement_api._approved_budget pattern); a code with no root row has no
    declarations. Sorted, distinct."""
    filters = {"docstatus": 1}
    if group:
        names = [row["name"] for row in frappe.get_all(
            "Consolidation Group",
            filters={"consolidation_group": group, "data_area_id": ["is", "not set"]},
            fields=["name"], limit_page_length=0)]
        if not names:
            return []
        filters["consolidation_group"] = ["in", names]
    rows = frappe.get_all("Approved Budget", filters=filters, fields=["fiscal_year"], limit_page_length=0)
    return sorted({int(row["fiscal_year"]) for row in rows})


@frappe.whitelist(methods=["GET"])
def period_strip_info(consolidation_group=None):
    if not _may_open_a_period_report():
        raise frappe.PermissionError
    period_rows = fiscal_calendar.fiscal_period_rows()
    # The Link filter holds the document name; the warehouse holds the code.
    group = _group_code(consolidation_group) if consolidation_group else None
    where = _GROUP_WHERE if group else ""
    params = {"group": group} if group else None

    def read(sql, pick):
        try:
            data = ch_read.rows(sql.format(where=where), params,
                                timeout=3, settings={"max_execution_time": 2})
            return [pick(r) for r in data]
        except Exception:  # noqa: BLE001 — "can't say" means no data, never an error
            return None

    with_data = read(_PERIODS_SQL, lambda r: (int(r["fiscal_year"]), int(r["fiscal_period"])))
    data_years = read(_YEARS_SQL, lambda r: int(r["fiscal_year"])) or []
    budget_years = _approved_budget_years(group)
    return period_strip_model.strip_info(period_rows, with_data, sorted(set(data_years) | set(budget_years)))

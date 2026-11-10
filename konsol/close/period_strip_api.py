"""Period strips endpoint: ``period_strip_info()`` (GET) returns the declared
fiscal years and the latest period with data (``period_strip_model.strip_info``)
so the Desk Year and Period strips can mark years and open on a period.

One warehouse read; if it fails (warehouse not built, any error) the latest
with data is None and the model falls back to the latest declared Regular
period. Never raises for a warehouse failure.
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
    get_report_doc): ``Report.is_permitted()`` (Has Role / Custom Role) and
    the ``report`` right on the report's reference doctype."""
    for name in _REPORTS:
        try:
            doc = frappe.get_doc("Report", name)
        except frappe.DoesNotExistError:
            continue
        if doc.is_permitted() and frappe.has_permission(doc.ref_doctype, "report"):
            return True
    return False


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
    return period_strip_model.strip_info(period_rows, with_data, data_years)

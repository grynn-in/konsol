"""Period Close: the close app's checks and sign-off state, per period, in Desk.

One row per Regular period of the chosen fiscal year (every Open period when
no year is chosen). The rows are the close app's own reads, laid out by
``period_close_report_model``: ``period_api.get_context`` (which periods),
``signoff_api.get_signoff`` (status, sign-off, next step, what blocks it) and
``checks_api.get_checks`` (latest run, ``can_run``). The report's buttons
(period_close.js) POST to the close app's own action endpoints; no rule is
decided here.

The roles are the ones all three read endpoints admit; ``ref_doctype`` is
EPM Fiscal Year, on which each of them has report permission.
"""
import frappe

from konsol.close import checks_api, period_api, period_close_report_model as model, signoff_api
from konsol.consolidation.report.consolidated_statement.consolidated_statement import _group_code


def execute(filters=None):
    filters = frappe._dict(filters or {})
    fiscal_year = frappe.utils.cint(filters.get("fiscal_year")) or None
    group = _group_code(filters.get("consolidation_group"))

    context = period_api.get_context()
    rows = [_row(state, group) for state in model.periods_for(context["periods"], fiscal_year)]
    return model.columns(), rows, model.message(fiscal_year, rows, context["config_gaps"])


def _row(state, group):
    fy, fp = int(state["key"][0]), int(state["key"][1])
    logged = len(frappe.local.message_log or [])
    try:
        signoff = signoff_api.get_signoff(fy, fp)
        checks = checks_api.get_checks(fy, fp)
    except frappe.ValidationError as e:
        # The endpoint's refusal for this period becomes the row's blocker,
        # shown as written; the other periods still show. Its message is not
        # also popped up.
        frappe.local.message_log = (frappe.local.message_log or [])[:logged]
        return model.row(state, None, None, None, group, error=str(e))
    return model.row(state, signoff, checks, _signed(signoff["checks"]["run"]), group)


def _signed(run):
    if not run:
        return None
    return frappe.db.get_value("Assertion Run", run, ["signed_off_by", "signed_off_at"], as_dict=True)

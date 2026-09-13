"""The group chart of accounts, read in one place (konsol#182).

Every konsol check that asks "is this account in the group chart?" asks
``chart_codes`` here: the trial balance upload (single and bulk), Consolidation
Group's intercompany-difference account and Intercompany Account's publish
check. Before this, each queried the warehouse's ERP-derived chart on its own,
and on a site whose warehouse had never built (a trial-balance-only site), that
raised UNKNOWN_TABLE and every one of them reported "ClickHouse is unreachable"
with no chart to fall back to.

Decided 13 Sep 2026: konsol defines the shape, and any source follows it. The
group chart is the Published Main Accounts and nothing else. There is no ERP
fallback: a site gets its chart by uploading it (konsol.chart_upload) or by
entering it in Desk, and an account that is not a Published Main Account is not
in the chart. Read from MariaDB with frappe.get_all (this is validation, not a
list a user sees), so validating a trial balance never needs the warehouse.
"""
import frappe

from konsol import group_chart_model as M

DOCTYPE = "Main Account"
#: What an account is, as the chart reader hands it out.
FIELDS = ("main_account", "account_name", "account_type", "statement_section", "normal_balance",
          "fx_method", "is_group", "is_posting", "is_suspended", "allow_ic", "main_account_category")
_CHECKS = ("is_group", "is_posting", "is_suspended", "allow_ic")


def chart_accounts() -> dict:
    """{code: account} of the Published Main Accounts, headings included.

    Empty on a site the doctype has not reached yet (a hot-copied release
    before migrate): every account is then outside the chart, and a trial
    balance is refused rather than accepted unvalidated.
    """
    if not frappe.db.table_exists(DOCTYPE):
        return {}
    out = {}
    for r in frappe.get_all(DOCTYPE, filters={"status": M.PUBLISHED}, fields=list(FIELDS), limit_page_length=0):
        account = {f: (M.flag(r.get(f)) if f in _CHECKS else M.text(r.get(f))) for f in FIELDS}
        out[account["main_account"]] = account
    return out


def posting_codes(chart):
    """The codes a trial balance may post to: every account but the headings."""
    return {c for c, a in chart.items() if not a["is_group"]}


def chart_codes():
    """Membership, for the callers that only ask whether a code is in the chart."""
    return posting_codes(chart_accounts())


@frappe.whitelist(methods=["GET"])
def get_chart(chart_of_accounts=None):
    """The Main Accounts the user may read, parent-first (get_list: permissions apply)."""
    filters = {"chart_of_accounts": chart_of_accounts} if chart_of_accounts else {}
    return frappe.get_list(DOCTYPE, filters=filters, order_by="lft asc", limit_page_length=0,
                           fields=["name", "status", "source", *M.DECLARED_FIELDS])

"""Fill Cash Flow Category from the published group chart (konsol#196).

dbt reads only epm_staging.cash_flow_categories for the cash-flow statement,
so a site that uploaded a complete chart (Balance Sheet leaves with
cf_category and cf_line_item) still failed the full build until someone
re-keyed the same mapping as Cash Flow Category rows. From now on Main
Account mirrors its mapping on every save; this patch does the same once for
what is already Published.

For every Published Main Account with a mapping (group_chart_model.
cash_flow_mapping): an account with a live (non-Inactive) Cash Flow Category
row is left alone; an account whose row is Inactive has that row republished
with the chart's mapping (Cash Flow Category refuses a second live row per
account, and the Inactive row may carry another name, so inserting beside it
would collide); an account with no row gets a Published row inserted. A
second run changes nothing.

patches.txt has no sections, so this runs pre_model_sync: it reloads both
doctypes before any query.
"""
import frappe

from konsol import group_chart_model as M

#: What cash_flow_mapping reads, plus the name.
FIELDS = ["name", "main_account", "is_group", "statement_section", "cf_category", "cf_line_item",
          "is_cash"]


def execute():
    frappe.reload_doc("epm", "doctype", "cash_flow_category")
    frappe.reload_doc("epm", "doctype", "main_account")

    # One row per account: validate() allows at most one live row, and a
    # later Inactive row is preferred only when no live one exists.
    rows = {}
    for r in frappe.get_all("Cash Flow Category", fields=["name", "main_account", "status"]):
        have = rows.get(r["main_account"])
        if have is None or have["status"] == "Inactive":
            rows[r["main_account"]] = r

    inserted = republished = 0
    for row in frappe.get_all("Main Account", filters={"status": M.PUBLISHED}, fields=FIELDS):
        mapping = M.cash_flow_mapping(row)
        if not mapping:
            continue
        existing = rows.get(mapping["main_account"])
        if existing is not None and existing["status"] != "Inactive":
            continue
        if existing is not None:
            doc = frappe.get_doc("Cash Flow Category", existing["name"])
            doc.update(dict(mapping, status=M.PUBLISHED))
            doc.save(ignore_permissions=True)
            republished += 1
        else:
            doc = frappe.get_doc(dict(mapping, doctype="Cash Flow Category", status=M.PUBLISHED))
            doc.insert(ignore_permissions=True)
            inserted += 1

    print("fill_cash_flow_categories_from_chart: %d Cash Flow Category row(s) inserted, %d republished"
          % (inserted, republished))

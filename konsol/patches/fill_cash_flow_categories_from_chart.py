"""Fill Cash Flow Category from the published group chart (konsol#196).

dbt reads only epm_staging.cash_flow_categories for the cash-flow statement,
so a site that uploaded a complete chart (Balance Sheet leaves with
cf_category and cf_line_item) still failed the full build until someone
re-keyed the same mapping as Cash Flow Category rows. From now on Main
Account mirrors its mapping on every save; this patch does the same once for
what is already Published.

For every Published Main Account with a mapping (group_chart_model.
cash_flow_mapping) and no live (non-Inactive) Cash Flow Category row, it
inserts a Published row with sign "1" (no dbt model reads sign). Accounts
that already have a live row are left alone, so a second run inserts nothing.

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

    live = set(frappe.get_all("Cash Flow Category", filters={"status": ["!=", "Inactive"]},
                              pluck="main_account"))
    inserted = 0
    for row in frappe.get_all("Main Account", filters={"status": M.PUBLISHED}, fields=FIELDS):
        mapping = M.cash_flow_mapping(row)
        if not mapping or mapping["main_account"] in live:
            continue
        doc = frappe.get_doc(dict(mapping, doctype="Cash Flow Category", sign="1", status=M.PUBLISHED))
        doc.insert(ignore_permissions=True)
        live.add(mapping["main_account"])
        inserted += 1

    print("fill_cash_flow_categories_from_chart: %d Cash Flow Category row(s) inserted" % inserted)

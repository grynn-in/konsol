"""Report Published budget dimensions that are not in the trial balance (BUD-8-2).

A Published Dimension with in_budget = 1 and in_trial_balance = 0 has budget
rows that no actual can be compared with. This patch only reports: it names
each one on the migrate log and states the two fixes. It changes nothing; the
site decides which fix applies.
"""
import frappe


def execute():
    names = [
        d["name"]
        for d in frappe.get_all(
            "Dimension",
            filters={"status": "Published", "in_budget": 1, "in_trial_balance": 0},
            fields=["name"],
            order_by="name",
        )
    ]
    if not names:
        print("report_budget_dimensions_not_in_tb: no Published budget dimension is missing from the trial balance.")
        return
    print(
        "report_budget_dimensions_not_in_tb: %d Published dimension(s) are In Budget but not In Trial Balance:"
        % len(names)
    )
    for name in names:
        print("  - %s" % name)
    print(
        'Fix each one either way: tick "In Trial Balance", or untick "In Budget". Nothing was changed.'
    )

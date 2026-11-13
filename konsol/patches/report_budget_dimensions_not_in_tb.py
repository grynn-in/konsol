"""Report Published budget dimensions that are not in the trial balance (BUD-8-2).

A Published Dimension with in_budget = 1 and in_trial_balance = 0 has budget
rows that no actual can be compared with. This patch only reports: it names
each one on the migrate log and states the three fixes. It changes nothing; the
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
    message = (
        "report_budget_dimensions_not_in_tb: %d Published dimension(s) have Include in Budget "
        "ticked but Include in Trial Balance unticked:\n%s\n"
        "Fix each one in one of three ways: tick Include in Trial Balance (its name must then "
        "start with dim_), untick Include in Budget, or unpublish it. Nothing was changed."
        % (len(names), "\n".join("  - %s" % n for n in names))
    )
    print(message)
    frappe.log_error(title="report_budget_dimensions_not_in_tb", message=message)

"""ytd_net_amount is a running balance, so it aggregates as `last`, not `sum`.

Defaults only seed new sites, so an existing site still declares
``cube_type: sum`` for it — and now that the read path honours the
declaration (konsol#251), leaving it as `sum` is exactly the bug: a range read
adds the balances together.

Measured on the live site before this ran:

    K.EPM("JP_ECL", 2025, "FY", "3100", "ytd_net_amount")
      -> -9,825,228,728.88, the sum of the six cumulative balances at
         periods 7-12, where the balance at period 12 is 449,131,396.42.

The expression moves with it. It feeds the Cube/dbt config, which a rerouted
measure never enters (`_trial_balance_measure_names` excludes it because
cumulative_balance is not computable in gold_trial_balance), so it was stating
an aggregation nothing performed; left as `sum(...)` beside `cube_type: last`
it would read as a second, contradicting source of truth.
"""
import frappe

_MEASURE = "ytd_net_amount"
_CUBE_TYPE = "last"
_EXPRESSION = "argMax(cumulative_balance, fiscal_period)"


def execute():
    if not frappe.db.table_exists("Measure"):
        return
    if not frappe.db.exists("Measure", _MEASURE):
        return
    doc = frappe.get_doc("Measure", _MEASURE)
    if doc.cube_type == _CUBE_TYPE and doc.expression == _EXPRESSION:
        return
    doc.cube_type = _CUBE_TYPE
    doc.expression = _EXPRESSION
    doc.save()
    frappe.db.commit()

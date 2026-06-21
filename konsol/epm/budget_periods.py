"""Wide budget period column names — the single source of truth.

Frappe-free on purpose: both the doctype controllers (which import
``frappe.model.document``) and the D365 mapping layer (unit-tested without a
live Frappe site) import ``PERIOD_FIELDS`` from here, so the wide layout has one
canonical definition without coupling the mapping code to frappe.
"""

# period_01 .. period_12, in fiscal order.
PERIOD_FIELDS = tuple("period_%02d" % n for n in range(1, 13))

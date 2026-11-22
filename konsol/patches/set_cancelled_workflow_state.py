"""Put already-cancelled documents in their workflow's docstatus-2 state (#529).

Chosen on #529 under the standing rule, option (b): before_cancel now sets the
installed workflow's docstatus-2 state, so a cancelled Business Combination,
Business Disposal, Consolidation Journal or Budget Cycle no longer keeps its
approved state. Rows cancelled before the fix still do; this repairs them.

Per doctype in konsol.workflows.INSTALLED: through the one helper,
cancel_target, read the site's active Workflow (a site may have renamed the
states, so no name is written here); then every docstatus-2 row whose state
field does not hold a docstatus-2 state (including a blank one: a blank Link
is NULL) gets the first. A doctype with no active workflow, or one with no
docstatus-2 state (Build Approval), is left alone. ``modified`` is left alone.
Idempotent: a second run finds nothing to write.
konsol/tests/test_cancelled_workflow_state.py runs it against a stub site.
"""
import frappe

from konsol import workflows


def execute():
    for doctype in workflows.INSTALLED:
        if not frappe.db.exists("DocType", doctype) or not frappe.db.table_exists(doctype):
            continue
        target = workflows.cancel_target(doctype)
        if not target:
            continue
        field, states = target
        for row in frappe.get_all(doctype, filters={"docstatus": 2}, fields=["name", field]):
            if row.get(field) not in states:
                frappe.db.set_value(doctype, row["name"], field, states[0], update_modified=False)

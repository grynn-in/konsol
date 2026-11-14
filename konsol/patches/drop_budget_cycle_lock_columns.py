"""Drop Budget Cycle's retired lock columns (konsol#348 S2-C W3).

S2-C W2a made the Cycle a workflow approval (BUD-9-3): submit is the approval
and the state lives in `workflow_state`. Its deadline-lock fields `status`,
`locked_by` and `locked_on` left the doctype, and nothing reads them (W2a
moved every reader to docstatus). Frappe never drops a column whose field has
left the JSON, so every older site would keep them in `tabBudget Cycle`:
invisible to the ORM, readable by raw SQL, and different from a fresh install.

Guarded like drop_cash_flow_category_sign: only the columns that exist are
dropped, so a second run is a no-op; a site without the table is left alone;
a failing drop is logged as a warning and not raised, because an unread
column left in place must never fail a migrate.
"""
import frappe

DOCTYPE = "Budget Cycle"
COLUMNS = ("status", "locked_by", "locked_on")


def execute():
    if not frappe.db.table_exists(DOCTYPE):
        return
    present = [c for c in COLUMNS if frappe.db.has_column(DOCTYPE, c)]
    if not present:
        return
    named = ", ".join(present)
    try:
        frappe.db.sql_ddl(f"alter table `tab{DOCTYPE}` " + ", ".join(f"drop column `{c}`" for c in present))
        frappe.logger().info(f"konsol#348 S2-C W3: dropped {DOCTYPE} {named}")
    except Exception:  # noqa: BLE001 — a failed drop must not fail a migrate
        frappe.logger().warning(
            f"konsol#348 S2-C W3: could not drop {DOCTYPE} {named}; they are unread but still present",
            exc_info=True)

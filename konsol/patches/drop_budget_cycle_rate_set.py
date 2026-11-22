"""Drop Budget Cycle's retired ``rate_set`` column (konsol#592 pair 1, T25).

T22 moved budget and forecast translation to the year's Constant rate sheet
(D2, Deepak Pai 22 Nov 2026): a Budget Cycle no longer names a Rate Set, and
its field left the doctype. Frappe never drops a column whose field has left
the JSON, so every older site would keep it in `tabBudget Cycle`: invisible
to the ORM, readable by raw SQL, and different from a fresh install.

Only Budget Cycle's column (coordinator's T25 scope decision, 22 Nov 2026):
Approved Budget and Current Forecast keep ``rate_set`` as a read-only field,
always CONSTANT, because statement_api, approved_budget_model and the
migration fallback read it; both leave in pair 2 with the Rate Set.

Runs after ``migrate_group_exchange_rates_to_rate_sheets`` (patches.txt),
which reads this column to find each year's budget Rate Set. Guarded like
drop_budget_cycle_lock_columns: no table or no column, no DDL, so a second
run is a no-op; a failing drop is logged and not raised, because an unread
column left in place must never fail a migrate.
"""
import frappe

DOCTYPE = "Budget Cycle"
COLUMN = "rate_set"


def execute():
    if not frappe.db.table_exists(DOCTYPE) or not frappe.db.has_column(DOCTYPE, COLUMN):
        return
    try:
        frappe.db.sql_ddl(f"alter table `tab{DOCTYPE}` drop column `{COLUMN}`")
        frappe.logger().info(f"konsol#592 T25: dropped {DOCTYPE}.{COLUMN}")
    except Exception:  # noqa: BLE001 — a failed drop must not fail a migrate
        frappe.logger().warning(
            f"konsol#592 T25: could not drop {DOCTYPE}.{COLUMN}; it is unread but still present",
            exc_info=True)

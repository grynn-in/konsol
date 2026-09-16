"""Drop the retired `Cash Flow Category.sign` column (konsol#197).

`sign` was a required Select (1 / -1) that nothing read: no dbt model selected
it — the cash-flow models read is_cash, cf_category and cf_line_item and negate
the signed movement themselves — and the chart mirror just wrote "1". The field
has left the doctype, but Frappe adds and alters columns on migrate and never
drops one whose field has left the JSON, so every pre-#197 site would keep the
value in `tabCash Flow Category` indefinitely: invisible to the ORM, readable by
raw SQL, and different from what a fresh install has.

Guarded like `lift_ownership_to_ownership_period`: no column, no DDL, so a
second run is a no-op; a failing drop is logged and swallowed, because an
unused column left in place must never fail a migrate.
"""
import frappe

DOCTYPE = "Cash Flow Category"
COLUMN = "sign"


def execute():
    if not frappe.db.has_column(DOCTYPE, COLUMN):
        return
    try:
        frappe.db.sql_ddl(f"alter table `tab{DOCTYPE}` drop column `{COLUMN}`")
        frappe.logger().info(f"konsol#197: dropped {DOCTYPE}.{COLUMN}")
    except Exception:  # noqa: BLE001 — a failed drop must not fail a migrate
        frappe.logger().warning(
            f"konsol#197: could not drop {DOCTYPE}.{COLUMN}; it is unused but "
            f"still present", exc_info=True)

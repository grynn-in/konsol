"""Retire Budget Annual Input's two fixed dimension columns (konsol#287).

The doctype used to carry two standard Data fields named after one customer's
cost centres and departments. konsol#287 removed them: the doctype now carries
the site's declared budget dimensions as Custom Fields, as Budget Line does.
Frappe never drops a column whose field has left the JSON, so this patch
decides what happens to each of the two, per site, from what it holds:

- **Absent**: nothing to do.
- **Declared**: a Published Dimension of that name is ticked in_budget. The
  column is kept. The Custom Field sync that runs after migrate creates the
  field of the same name over it, so the values are read again unchanged.
- **Empty**: no row holds a value. The column is dropped, so the table matches
  a fresh install's.
- **Holding values, not declared**: the column is kept and the count is logged.
  gold_spread_budget only ever selected the declared budget dimensions, so
  these values never reached a number, and dropping them would destroy data
  that ticking the dimension in_budget brings back.

Runs before the doctype sync (patches.txt has no sections, so every patch is
pre-model-sync), though nothing here depends on the field still being in meta:
it reads and drops columns with SQL. A second run finds the column gone or kept
and changes nothing. A failure is logged and swallowed: a column left in place
must never fail a migrate.
"""
import frappe

DOCTYPE = "Budget Annual Input"
#: The two fixed fields konsol#287 removed. A patch records a moment in a
#: site's history, so it names them; the konsol#287 guard allow-lists them here.
RETIRED = ("dim_cost_center", "dim_department")


def execute():
    for column in RETIRED:
        try:
            _retire(column)
        except Exception:  # noqa: BLE001 — a column left in place must not fail a migrate
            frappe.logger().warning(
                f"konsol#287: could not retire {DOCTYPE}.{column}; left in place",
                exc_info=True)


def _retire(column):
    if not frappe.db.has_column(DOCTYPE, column):
        return
    if frappe.db.exists("Dimension", {"dimension_name": column, "in_budget": 1,
                                      "status": "Published"}):
        return  # the Custom Field sync adopts the column and its values
    held = frappe.db.sql(
        f"select count(*) from `tab{DOCTYPE}` "
        f"where `{column}` is not null and `{column}` != ''")[0][0]
    if held:
        frappe.logger().warning(
            f"konsol#287: {DOCTYPE}.{column} is no longer a field but {held} "
            f"row(s) hold a value in it. The column is kept. Publish a Dimension "
            f"named {column} with Include in Budget ticked to read them again.")
        return
    frappe.db.sql_ddl(f"alter table `tab{DOCTYPE}` drop column `{column}`")
    frappe.logger().info(f"konsol#287: dropped the empty {DOCTYPE}.{column}")

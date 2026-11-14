"""Drop epm_gold.budget_monthly_input (konsol#348 S2-C Z6, closes #282 with T0).

Budget Sheet wrote it, and S2-C Z1 retired Budget Sheet: Budget Entry writes
epm_raw.budget_entries instead. konsolidat#356 took the table out of the
warehouse contract and gold_spread_budget no longer reads it, and konsol no
longer creates it (_REFERENCE_TABLE_DDL). Left in place it would be an unread
table holding the last Sheet figures, so this patch drops it.

``IF EXISTS``, so a rerun or a warehouse that never had it is a no-op. A failed
drop (ClickHouse unreachable during the migrate, say) is printed and logged as
a warning, not raised: an unread table must never fail a migrate. Run
``DROP TABLE IF EXISTS epm_gold.budget_monthly_input`` by hand to finish it.
"""
import frappe

TABLE = "epm_gold.budget_monthly_input"


def execute():
    from konsol.clickhouse import execute as ch_execute

    try:
        ch_execute(f"DROP TABLE IF EXISTS {TABLE}")
        frappe.logger().info(f"konsol#348 S2-C Z6: dropped {TABLE}")
    except Exception:  # noqa: BLE001 — a failed drop must not fail a migrate
        message = f"konsol#348 S2-C Z6: could not drop {TABLE}; it is unread but still present"
        print(message)
        frappe.logger().warning(message, exc_info=True)

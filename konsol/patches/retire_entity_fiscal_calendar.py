"""Retire Entity Fiscal Calendar (konsol#577; Deepak Pai, 21 Nov 2026).

The doctype mapped each ERP data area to the ERP fiscal calendar it posts
against. Nothing has read that mapping since konsolidat#221 removed the ERP
calendar branch: the site declares one calendar (EPM Fiscal Year,
epm_staging.fiscal_periods) and silver_gl_entries dates every trial-balance
submission from it. A site could still fill the doctype in and believe it
changed something, so it is removed rather than left as dead input.

The doctype code left the app with this patch. Frappe's migrate leaves the
table, the Custom Fields and any Property Setter of a doctype that leaves the
app, so this patch removes them:

1. The Custom Fields and Property Setters on the doctype are deleted, and so
   are site rows elsewhere that name it: Link Custom Fields, Client Scripts,
   Custom DocPerms, User Permissions and Reports.
2. The DocType record is deleted.
3. ``tabEntity Fiscal Calendar`` is dropped. Its rows are not kept: nothing
   read them (zero customers on 21 Nov 2026).
4. The warehouse table epm_gold.entity_fiscal_calendars is dropped. A failed
   drop (ClickHouse unreachable during the migrate, say) is printed and logged
   as a warning, never raised: an unread table must not fail a migrate. The
   table is also in konsol.clickhouse._RETIRED_TABLES, so the next migrate's
   ensure_reference_tables drops it again and deletes its sync_watermark row.

Idempotent: ``delete_doc`` passes ``ignore_missing=True``, the deletes filter
by doctype and both drops are ``DROP TABLE IF EXISTS``, so a rerun or a fresh
install is a no-op.
"""
import frappe

DOCTYPE = "Entity Fiscal Calendar"
CH_TABLE = "epm_gold.entity_fiscal_calendars"


def execute():
    frappe.db.delete("Custom Field", {"dt": DOCTYPE})
    frappe.db.delete("Property Setter", {"doc_type": DOCTYPE})
    # Site rows on other doctypes that point at it; delete_doc removes only
    # Table-type Custom Fields and global search (review of #578).
    frappe.db.delete("Custom Field", {"options": DOCTYPE})
    frappe.db.delete("Client Script", {"dt": DOCTYPE})
    frappe.db.delete("Custom DocPerm", {"parent": DOCTYPE})
    frappe.db.delete("User Permission", {"allow": DOCTYPE})
    frappe.db.delete("Report", {"ref_doctype": DOCTYPE})
    frappe.delete_doc("DocType", DOCTYPE, ignore_missing=True, force=True)
    # delete_doc leaves the table of a standard DocType; drop it explicitly.
    frappe.db.sql_ddl(f"DROP TABLE IF EXISTS `tab{DOCTYPE}`")
    _drop_warehouse_table()


def _drop_warehouse_table():
    from konsol.clickhouse import execute as ch_execute

    try:
        ch_execute(f"DROP TABLE IF EXISTS {CH_TABLE}")
        frappe.logger().info(f"konsol#577: dropped {CH_TABLE}")
    except Exception:  # noqa: BLE001 — a failed drop must not fail a migrate
        message = (f"konsol#577: could not drop {CH_TABLE}; it is unread but still present. "
                   f"The next migrate drops it (_RETIRED_TABLES), or run "
                   f"DROP TABLE IF EXISTS {CH_TABLE} by hand.")
        print(message)
        frappe.logger().warning(message, exc_info=True)

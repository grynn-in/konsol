"""Retire Group Exchange Rate (konsol#592 pair 1, task T26).

Rate Sheet replaced it (Deepak Pai, 22 Nov 2026, #592): actuals rates are
held on one Statutory sheet per period and budget rates on one Constant
sheet per year. ``migrate_group_exchange_rates_to_rate_sheets`` (T25) moved
every row onto sheets and attached ONE audit CSV of every row, dropped ones
included, to Rate Set CONSTANT. This patch runs after it and after
``drop_budget_cycle_rate_set``.

The doctype code left the app with this patch. Frappe's migrate leaves the
table, the Custom Fields and any Property Setter of a doctype that leaves the
app, so this patch removes them:

1. **Refuses an unmigrated table.** While ``tabGroup Exchange Rate`` has rows
   and the migration has left neither of its markers (a Migrated Rate Sheet
   Row, or the audit file on CONSTANT: the markers its own idempotency
   reads), the migrate stops here, naming the reason, and nothing is
   deleted. An unmigrated table is never dropped.
2. The site rows that name the doctype are deleted: Custom Fields, Property
   Setters, Link Custom Fields, Client Scripts, Custom DocPerms, User
   Permissions, Reports, Number Cards, Dashboard Charts, Workflow Actions and
   any Workflow a site defined on it (konsol shipped none).
3. The DocType record is deleted, then ``tabGroup Exchange Rate`` is dropped.

**ClickHouse is never touched.** ``epm_staging.group_exchange_rates`` keeps
its name: it is the live warehouse rates table, now published from the
approved sheets (``group_rates.publish_rates``).

Close Events, Versions and Comments that name GER documents stay: the trail
resolves a retired name to the Rate Sheet that holds it (T23).

Idempotent: ``delete_doc`` passes ``ignore_missing=True``, the deletes filter
by doctype and the drop is ``DROP TABLE IF EXISTS``, so a rerun or a fresh
install is a no-op.
"""
import frappe

DOCTYPE = "Group Exchange Rate"
#: The migration's markers (migrate_group_exchange_rates_to_rate_sheets).
MIGRATED_SOURCE = "Migrated"
AUDIT_FILE = "group-exchange-rate-migration-audit.csv"
CONSTANT_RATE_SET = "CONSTANT"


def execute():
    _refuse_unmigrated()
    frappe.db.delete("Custom Field", {"dt": DOCTYPE})
    frappe.db.delete("Property Setter", {"doc_type": DOCTYPE})
    # Site rows on other doctypes that point at it; delete_doc removes only
    # Table-type Custom Fields and global search (review of #578).
    frappe.db.delete("Custom Field", {"options": DOCTYPE})
    frappe.db.delete("Client Script", {"dt": DOCTYPE})
    frappe.db.delete("Custom DocPerm", {"parent": DOCTYPE})
    frappe.db.delete("User Permission", {"allow": DOCTYPE})
    frappe.db.delete("Report", {"ref_doctype": DOCTYPE})
    frappe.db.delete("Number Card", {"document_type": DOCTYPE})
    frappe.db.delete("Dashboard Chart", {"document_type": DOCTYPE})
    frappe.db.delete("Workflow Action", {"reference_doctype": DOCTYPE})
    # A Workflow's states and transitions are child rows: delete_doc takes them.
    for name in frappe.get_all("Workflow", filters={"document_type": DOCTYPE}, pluck="name"):
        frappe.delete_doc("Workflow", name, ignore_missing=True, force=True)
    frappe.delete_doc("DocType", DOCTYPE, ignore_missing=True, force=True)
    # delete_doc leaves the table of a standard DocType; drop it explicitly.
    frappe.db.sql_ddl(f"DROP TABLE IF EXISTS `tab{DOCTYPE}`")
    print(f"konsol#592: {DOCTYPE} retired; epm_staging.group_exchange_rates is kept")


def _refuse_unmigrated():
    if not frappe.db.table_exists(DOCTYPE):
        return
    rows = frappe.db.sql(f"SELECT COUNT(*) FROM `tab{DOCTYPE}`")[0][0]
    if not rows or _migrated():
        return
    frappe.throw(
        f"konsol#592: `tab{DOCTYPE}` still holds {rows} row(s) and the migration to Rate Sheets has not "
        "run (no Migrated Rate Sheet Row, and no migration audit file on Rate Set CONSTANT). The table "
        "is not dropped. Run konsol.patches.migrate_group_exchange_rates_to_rate_sheets (bench migrate "
        "runs it first), fix what it refuses, then migrate again.", frappe.ValidationError)


def _migrated():
    return bool(frappe.db.exists("Rate Sheet Row", {"parenttype": "Rate Sheet", "source": MIGRATED_SOURCE})
                or frappe.db.exists("File", {"attached_to_doctype": "Rate Set",
                                             "attached_to_name": CONSTANT_RATE_SET,
                                             "file_name": AUDIT_FILE}))

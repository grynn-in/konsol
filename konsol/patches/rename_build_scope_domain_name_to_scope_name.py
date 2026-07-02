import frappe
from frappe.model.utils.rename_field import rename_field


def execute():
    if not frappe.db.has_column("Build Scope", "domain_name"):
        # Already renamed (or fresh install) — nothing to do.
        return

    frappe.reload_doctype("Build Scope")

    if frappe.db.has_column("Build Scope", "scope_name"):
        # The schema sync (triggered by reload_doctype / the updated JSON) has
        # already added the new column, so rename_field would refuse. Copy the
        # data across and drop the orphaned old column.
        frappe.db.sql(
            "UPDATE `tabBuild Scope` SET scope_name = domain_name "
            "WHERE scope_name IS NULL OR scope_name = ''"
        )
        frappe.db.sql_ddl("ALTER TABLE `tabBuild Scope` DROP COLUMN domain_name")
    else:
        rename_field("Build Scope", "domain_name", "scope_name")

    frappe.clear_cache(doctype="Build Scope")

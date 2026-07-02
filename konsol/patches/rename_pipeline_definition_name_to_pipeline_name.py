import frappe
from frappe.model.utils.rename_field import rename_field


def execute():
    if not frappe.db.has_column("Pipeline", "definition_name"):
        return

    # Force the doctype (and its new pipeline_name column) to sync now so the
    # branch below sees the real post-sync column state in a single migrate pass.
    frappe.reload_doctype("Pipeline")

    if frappe.db.has_column("Pipeline", "pipeline_name"):
        # pipeline_name was (pre-)created by model sync — carry data over, then drop old.
        frappe.db.sql(
            "UPDATE `tabPipeline` SET pipeline_name = definition_name "
            "WHERE pipeline_name IS NULL OR pipeline_name = ''"
        )
        frappe.db.sql_ddl("ALTER TABLE `tabPipeline` DROP COLUMN definition_name")
    else:
        rename_field("Pipeline", "definition_name", "pipeline_name")

    frappe.clear_cache(doctype="Pipeline")

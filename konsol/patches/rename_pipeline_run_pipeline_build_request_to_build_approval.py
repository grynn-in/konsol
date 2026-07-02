import frappe
from frappe.model.utils.rename_field import rename_field


def execute():
    if not frappe.db.has_column("Pipeline Run", "pipeline_build_request"):
        # Already renamed (or fresh install) — nothing to do.
        return

    frappe.reload_doctype("Pipeline Run")

    if frappe.db.has_column("Pipeline Run", "build_approval"):
        # The schema sync (triggered by reload_doctype / the updated JSON) has
        # already added the new column, so rename_field would refuse. Copy the
        # data across and drop the orphaned old column.
        frappe.db.sql(
            "UPDATE `tabPipeline Run` "
            "SET build_approval = pipeline_build_request "
            "WHERE build_approval IS NULL AND pipeline_build_request IS NOT NULL"
        )
        frappe.db.sql_ddl(
            "ALTER TABLE `tabPipeline Run` DROP COLUMN pipeline_build_request"
        )
    else:
        rename_field("Pipeline Run", "pipeline_build_request", "build_approval")

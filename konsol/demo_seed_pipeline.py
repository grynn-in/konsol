"""One-shot local demo: fix EPM paths, mark connectors synced, seed PBRs.

Run: bench --site konsolidat.local execute konsol.demo_seed_pipeline.seed
"""
import frappe
from frappe.utils import now_datetime


def _fix_local_epm_settings():
    settings = frappe.get_single("EPM Settings")
    settings.dbt_project_path = "/home/frappe/dbt_project"
    settings.clickhouse_host = settings.clickhouse_host or "clickhouse"
    settings.last_airbyte_sync_status = "Success"
    settings.last_airbyte_sync_at = now_datetime()
    settings.last_airbyte_sync_rows = 862
    settings.flags.ignore_permissions = True
    settings.save()

    for conn in frappe.get_all("Connector", filters={"enabled": 1}, pluck="name"):
        frappe.db.set_value(
            "Connector", conn,
            {
                "last_sync_status": "Success",
                "last_sync_at": now_datetime(),
                "last_sync_rows": 862,
            },
        )


def seed():
    frappe.set_user("Administrator")
    _fix_local_epm_settings()
    from konsol.schema_lifecycle import request_governed_rebuild

    created = []

    if frappe.db.exists("Reporting Hierarchy", "MGMT_DEMO"):
        rh = frappe.get_doc("Reporting Hierarchy", "MGMT_DEMO")
        name = request_governed_rebuild(rh, "Demo seed", scope="reporting")
        created.append(name)

    for scope in ("actuals", "consolidation", "scenarios"):
        existing = frappe.get_all(
            "Pipeline Build Request",
            filters={"build_scope": scope, "workflow_state": ["in", [
                "Draft", "Pending Review", "Approved", "Running",
            ]]},
            limit=1,
        )
        if existing:
            created.append(existing[0].name)
            continue
        pbr = frappe.new_doc("Pipeline Build Request")
        pbr.build_scope = scope
        pbr.trigger_source = "manual"
        pbr.requested_by = frappe.session.user
        pbr.insert(ignore_permissions=True)
        created.append(pbr.name)

    for name in frappe.get_all(
        "Pipeline Build Request",
        filters={"workflow_state": "Pending Review"},
        pluck="name",
    ):
        doc = frappe.get_doc("Pipeline Build Request", name)
        doc.workflow_state = "Approved"
        doc.approved_by = frappe.session.user
        doc.save(ignore_permissions=True)

    frappe.db.commit()
    return {"created_or_reused": created, "pending_approved": True}
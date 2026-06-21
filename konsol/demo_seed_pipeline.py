"""One-shot local demo: fix EPM paths, mark connectors synced, seed PBRs.

Run full demo seed:
  bench --site konsolidat.local execute konsol.demo_seed_pipeline.seed

Sync allocation config only (after fixture import / migrate):
  bench --site konsolidat.local execute konsol.demo_seed_pipeline.sync_config
"""
import frappe
from frappe.utils import now_datetime

_ACTIVE_PBR_STATES = ("Draft", "Pending Review", "Approved", "Running")


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
            "Connector",
            conn,
            {
                "last_sync_status": "Success",
                "last_sync_at": now_datetime(),
                "last_sync_rows": 862,
            },
        )


def _sync_allocation_config():
    """Fixture import skips DocType hooks — push rules/drivers/runs to ClickHouse."""
    try:
        from konsol.allocation.bootstrap import sync_allocation_config_to_clickhouse

        sync_allocation_config_to_clickhouse()
        return True
    except Exception:
        frappe.logger().warning(
            "allocation config ClickHouse sync skipped in demo_seed_pipeline",
            exc_info=True,
        )
        return False


def sync_config():
    """Best-effort: EPM paths + connector status + allocation staging sync."""
    frappe.set_user("Administrator")
    _fix_local_epm_settings()
    synced = _sync_allocation_config()
    frappe.db.commit()
    return {"allocation_synced": synced}


def _scope_needs_pbr(scope):
    """Return True unless a build for this scope is already in flight."""
    in_flight = frappe.db.get_value(
        "Pipeline Build Request",
        {"build_scope": scope, "workflow_state": ["in", list(_ACTIVE_PBR_STATES)]},
        "name",
    )
    if in_flight:
        return False
    if scope != "consolidation":
        return True
    # Re-run consolidation when the latest attempt failed (common after alloc gaps).
    last_state = frappe.db.get_value(
        "Pipeline Build Request",
        {"build_scope": "consolidation"},
        "workflow_state",
        order_by="creation desc",
    )
    return last_state != "Completed"


def _approve_pending_for_scope(scope):
    """Approve pending PBRs for one scope so builds run in dependency order."""
    for name in frappe.get_all(
        "Pipeline Build Request",
        filters={"build_scope": scope, "workflow_state": "Pending Review"},
        pluck="name",
    ):
        doc = frappe.get_doc("Pipeline Build Request", name)
        doc.workflow_state = "Approved"
        doc.approved_by = frappe.session.user
        doc.save(ignore_permissions=True)


def _create_scope_pbr(scope):
    if not _scope_needs_pbr(scope):
        last = frappe.db.get_value(
            "Pipeline Build Request",
            {"build_scope": scope},
            "name",
            order_by="creation desc",
        )
        return last, False
    pbr = frappe.new_doc("Pipeline Build Request")
    pbr.build_scope = scope
    pbr.trigger_source = "manual"
    pbr.requested_by = frappe.session.user
    pbr.insert(ignore_permissions=True)
    return pbr.name, True


def seed():
    frappe.set_user("Administrator")
    _fix_local_epm_settings()
    allocation_synced = _sync_allocation_config()
    from konsol.schema_lifecycle import request_governed_rebuild

    created = []

    # Order matters: reporting models depend on actuals/scenarios upstream.
    for scope in ("actuals", "scenarios", "consolidation"):
        name, _is_new = _create_scope_pbr(scope)
        created.append(name)
        _approve_pending_for_scope(scope)

    if frappe.db.exists("Reporting Hierarchy", "MGMT_DEMO"):
        rh = frappe.get_doc("Reporting Hierarchy", "MGMT_DEMO")
        name = request_governed_rebuild(rh, "Demo seed", scope="reporting")
        created.append(name)
        _approve_pending_for_scope("reporting")

    frappe.db.commit()
    return {
        "created_or_reused": created,
        "allocation_synced": allocation_synced,
        "pending_approved": True,
    }
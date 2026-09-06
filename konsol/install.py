"""
Post-install setup for Konsolidat.
Called by the Docker configurator to set up EPM Settings with ClickHouse connection.
Also creates EPM roles on migrate.
"""
import frappe


def setup_epm_settings(
    ch_host="localhost",
    ch_port=8123,
    ch_user="default",
    ch_password="open_epm_dev",
    dbt_project_path="/home/frappe/dbt_project",
):
    """Configure EPM Settings with ClickHouse connection details."""
    if not frappe.db.exists("DocType", "EPM Settings"):
        frappe.logger().warning("EPM Settings doctype not found. Skipping setup.")
        return

    settings = frappe.get_single("EPM Settings")
    settings.clickhouse_host = ch_host
    settings.clickhouse_port = int(ch_port)
    settings.clickhouse_user = ch_user
    settings.clickhouse_password = ch_password
    settings.dbt_project_path = dbt_project_path
    settings.flags.ignore_permissions = True
    settings.save()
    frappe.db.commit()
    frappe.logger().info(f"EPM Settings configured: ClickHouse at {ch_host}:{ch_port}")


def after_migrate():
    """Called after bench migrate — ensures EPM roles exist, the dimension
    crosswalk seed reflects fixture-loaded Dimension Mapping docs, allocation
    config is synced to ClickHouse, and the Konsolidat desk workspace is present."""
    _restore_asset_manifest()
    _create_roles()
    _regenerate_dimension_mappings_seed()
    _regenerate_cash_flow_categories_seed()
    _regenerate_reporting_hierarchies_seed()
    _sync_allocation_config_to_clickhouse()
    _setup_dashboard()
    _retire_konsol_control_page()
    _sync_budget_line_custom_fields()
    _bootstrap_budget_fixtures()


def _restore_asset_manifest():
    """Restore image-baked assets.json and evict Redis assets_json after rebuild.

    The hashed JS/CSS bundles live in the image layer; the manifest on the
    persistent sites/ volume goes stale after ``docker compose build``. Frappe
    serves HTML from the Redis ``assets_json`` key, so copying the file alone
    is not enough — the key must be deleted too (init.sh does both on deploy).
    """
    import os
    import shutil

    baked = "/home/frappe/baked-assets/assets.json"
    if not os.path.isfile(baked):
        return

    bench_root = os.path.abspath(
        os.path.join(frappe.get_app_path("konsol"), "..", ".."))
    assets_dir = os.path.join(bench_root, "sites", "assets")
    try:
        os.makedirs(assets_dir, exist_ok=True)
        shutil.copy2(baked, os.path.join(assets_dir, "assets.json"))
        baked_rtl = "/home/frappe/baked-assets/assets-rtl.json"
        if os.path.isfile(baked_rtl):
            shutil.copy2(baked_rtl, os.path.join(assets_dir, "assets-rtl.json"))
    except Exception:
        frappe.logger().warning(
            "asset manifest restore skipped after migrate", exc_info=True)
        return

    try:
        import redis

        host = (
            frappe.conf.get("redis_cache")
            or os.environ.get("REDIS_CACHE_HOST", "redis_cache")
        )
        if isinstance(host, str) and host.startswith("redis://"):
            client = redis.from_url(host, socket_timeout=2)
        else:
            client = redis.Redis(host=host, port=6379, socket_timeout=2)
        client.delete("assets_json")
    except Exception:
        frappe.logger().warning(
            "assets_json redis eviction skipped after migrate", exc_info=True)


def _bootstrap_budget_fixtures():
    """Enrich fixture budget lines (dims) and sync demo sheets to ClickHouse."""
    try:
        from konsol.budget.bootstrap import (
            enrich_budget_fixture_lines,
            sync_budget_sheets_to_clickhouse,
        )
        enrich_budget_fixture_lines()
        sync_budget_sheets_to_clickhouse()
    except Exception as e:
        # Do not fail migrate, but make this visible: a silent skip here leaves
        # epm_gold.budget_monthly_input unpopulated, which breaks the whole
        # dbt gold layer downstream of gold_spread_budget.
        frappe.logger().error(
            "budget fixture bootstrap FAILED after migrate", exc_info=True)
        frappe.log_error(
            "install: budget fixture bootstrap failed after migrate", str(e))


def _sync_budget_line_custom_fields():
    """Provision Budget Line's in_budget dim Custom Fields after migrate.

    These columns are otherwise only synced on Dimension publish / manual schema
    apply, but the Excel budget write path (and the reshape migration) need them
    to exist. Best-effort — never fail a migrate over it.
    """
    try:
        from konsol.schema_apply import _sync_budget_custom_fields
        _sync_budget_custom_fields()
    except Exception:
        frappe.logger().warning(
            "budget line custom field sync skipped after migrate", exc_info=True)


def _retire_konsol_control_page():
    """Remove legacy desk page now replaced by /konsol-exec SPA."""
    try:
        if frappe.db.exists("Page", "konsol-control"):
            frappe.delete_doc("Page", "konsol-control", force=True, ignore_permissions=True)
    except Exception:
        frappe.logger().warning("konsol-control page retirement skipped", exc_info=True)


def _setup_dashboard():
    """Ensure the Konsolidat desk workspace exists. Best-effort — never fail a
    migrate over a desk convenience (e.g. a doctype not yet present)."""
    try:
        from konsol.dashboard import setup_workspace
        setup_workspace()
    except Exception:
        frappe.logger().warning(
            "Konsolidat workspace setup skipped after migrate",
            exc_info=True,
        )


def _sync_allocation_config_to_clickhouse():
    """Push fixture-loaded Allocation Rule/Driver docs to ClickHouse.

    Fixture import does not run ``on_update``, so staging would stay empty until
    a manual save. Best-effort — never fail migrate (e.g. CH not configured).
    """
    try:
        from konsol.allocation.bootstrap import sync_allocation_config_to_clickhouse

        sync_allocation_config_to_clickhouse()
    except Exception:
        frappe.logger().warning(
            "allocation config ClickHouse sync skipped after migrate",
            exc_info=True,
        )


def _regenerate_dimension_mappings_seed():
    """Repopulate seeds/dimension_mappings.csv after migrate.

    Fixture import inserts Dimension Mapping docs without running publish(), so
    the crosswalk seed would otherwise stay empty until a manual publish. This
    syncs it from the published docs. Best-effort — never fail a migrate over a
    dbt seed (e.g. dbt on another host / dir absent).
    """
    try:
        from konsol.dbt_config import regenerate_dimension_mappings_seed
        regenerate_dimension_mappings_seed()
    except Exception:
        frappe.logger().warning(
            "dimension_mappings seed regeneration skipped after migrate",
            exc_info=True,
        )


def _regenerate_cash_flow_categories_seed():
    """Repopulate seeds/cash_flow_categories.csv after migrate.

    Fixture import inserts Cash Flow Category docs without running publish(), so
    the seed would otherwise stay stale until a manual publish. Syncs it from the
    published docs. Best-effort — never fail a migrate over a dbt seed.
    """
    try:
        from konsol.dbt_config import regenerate_cash_flow_categories_seed
        regenerate_cash_flow_categories_seed()
    except Exception:
        frappe.logger().warning(
            "cash_flow_categories seed regeneration skipped after migrate",
            exc_info=True,
        )


def _regenerate_reporting_hierarchies_seed():
    """Sync reporting_hierarchies.csv after migrate (fixture-loaded hierarchies)."""
    try:
        from konsol.dbt_config import regenerate_reporting_hierarchies_seed
        regenerate_reporting_hierarchies_seed()
    except Exception:
        frappe.logger().warning(
            "reporting_hierarchies seed regeneration skipped after migrate",
            exc_info=True,
        )


def _create_roles():
    """Create EPM User, EPM Analyst, EPM Admin roles if they don't exist."""
    roles = {
        "EPM User": "Can save docs that trigger builds, read-only on build requests",
        "EPM Analyst": "Can create manual build requests, view build history",
        "EPM Admin": "Can approve high-risk builds, full pipeline access",
    }
    for role_name, desc in roles.items():
        if not frappe.db.exists("Role", role_name):
            role = frappe.new_doc("Role")
            role.role_name = role_name
            role.desk_access = 1
            role.description = desc
            role.insert(ignore_permissions=True)
            frappe.logger().info(f"Created role: {role_name}")
    frappe.db.commit()

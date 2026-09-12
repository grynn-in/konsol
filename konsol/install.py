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
    # F3: the three seed regenerators are gone — Dimension Mapping, Cash Flow
    # Category and Reporting Hierarchy write through to epm_staging like every
    # other governed table, and _reconcile_clickhouse() below re-syncs them
    # after fixture import (which does not fire on_update).
    _sync_allocation_config_to_clickhouse()
    # BEFORE the reconcile: a document saved during migrate does not reach
    # ClickHouse (sync_table no-ops while frappe.flags.in_migrate is set, which
    # is deliberate — EPM Settings may not be configured yet). reconcile_all
    # passes force=True, so it is the one call that can carry these rows
    # through; seeding after it left the periods in Frappe and nothing in the
    # warehouse, and assert_ownership_chain_complete failed on 108 rows.
    _bootstrap_ownership_periods()
    _bootstrap_budget_annual_input()
    _reconcile_clickhouse()
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


def _bootstrap_budget_annual_input():
    """Seed the demo annual budget rows — once, and only when there are none.

    Deliberately NOT a fixture: EPM Analyst can edit these, and everything in
    konsol/fixtures/ is force-deleted and reinserted on every migrate. A fixture
    would revert an analyst's revised annual figure three times a day, and the
    cycle-lock guard returns early under in_import so it would do it even for a
    Locked cycle. See konsol/demo_data/README.md.

    Before the reconcile, like the ownership periods: a document saved during
    migrate never reaches ClickHouse, so the forced sync is the one that carries
    it. Best-effort — never fail a migrate over demo data.
    """
    import json
    import os

    try:
        if frappe.db.count("Budget Annual Input"):
            return
        path = os.path.join(frappe.get_app_path("konsol"), "demo_data",
                            "budget_annual_input.json")
        if not os.path.isfile(path):
            return
        with open(path) as handle:
            rows = json.load(handle)
        for row in rows:
            if frappe.db.exists("Budget Annual Input", row.get("name")):
                continue
            doc = frappe.get_doc(dict(row))
            doc.flags.ignore_permissions = True
            doc.insert()
        frappe.logger().info(f"konsol: seeded {len(rows)} demo annual budget row(s)")
    except Exception:
        frappe.logger().warning(
            "annual budget bootstrap skipped after migrate", exc_info=True)


def _bootstrap_ownership_periods():
    """Seed the demo Ownership Periods — once, and only when there are none.

    Ownership Period is deliberately NOT in the ``fixtures`` hook. Fixture sync
    force-deletes and reinserts every shipped name on every migrate
    (import_fixtures -> import_doc -> delete_old_doc, which bypasses the
    submitted-document guard), so a shipped period would revert a user's edit —
    and the figures lift_ownership_to_ownership_period had just carried over
    from the tree — on the next migrate. Verified on the live stack: an edit
    from 80% to 65% was back at 80% after one `bench migrate`. That is the same
    "it re-ran and reverted the publish" failure F2 removes from the dbt side;
    ownership is transactional data, not configuration.

    So: a demo site with no ownership at all gets the demo set, and every other
    site is left alone. Best-effort — never fail a migrate over demo data.
    """
    import json
    import os

    try:
        if frappe.db.count("Ownership Period"):
            return
        # konsol/demo_data/, NOT konsol/fixtures/ — see that directory's README.
        # import_fixtures() imports every .json in fixtures/ on every migrate,
        # whatever the `fixtures` hook lists, force-deleting the existing
        # document first. Ownership is data, not configuration.
        path = os.path.join(frappe.get_app_path("konsol"), "demo_data",
                            "ownership_period.json")
        if not os.path.isfile(path):
            return
        with open(path) as f:
            rows = json.load(f)
        for row in rows:
            if frappe.db.exists("Ownership Period", row.get("name")):
                continue
            doc = frappe.get_doc(dict(row, docstatus=0))
            doc.flags.ignore_permissions = True
            doc.insert()
            doc.submit()
        frappe.logger().info(
            f"F2: seeded {len(rows)} demo ownership period(s)")
    except Exception:
        frappe.logger().warning(
            "ownership period bootstrap skipped after migrate", exc_info=True)


def _bootstrap_budget_fixtures():
    """Enrich fixture budget lines (dims) and sync demo sheets to ClickHouse."""
    try:
        from konsol.budget.bootstrap import (
            enrich_budget_fixture_lines,
            sync_budget_sheets_to_clickhouse,
        )
        enrich_budget_fixture_lines()
        sync_budget_sheets_to_clickhouse()
    except Exception:
        frappe.logger().warning(
            "budget fixture bootstrap skipped after migrate", exc_info=True)


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


def _reconcile_clickhouse():
    """Re-sync every write-through table after migrate.

    Document hooks cannot repair a table whose records disappeared without
    firing them — a fixture reload, a patch, or a site rebuilt against a
    ClickHouse volume that outlived it. Those rows then stay forever, because
    there is nothing left to edit that would trigger a sync.

    Best-effort — never fail a migrate over it.
    """
    try:
        from konsol.clickhouse import reconcile_all

        synced = reconcile_all()
        frappe.logger().info(
            f"reconcile: re-synced {len(synced)} write-through tables"
        )
    except Exception:
        frappe.logger().warning("ClickHouse reconcile skipped after migrate", exc_info=True)





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

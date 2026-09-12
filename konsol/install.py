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

    before = _warehouse_target()
    settings = frappe.get_single("EPM Settings")
    settings.clickhouse_host = ch_host
    settings.clickhouse_port = int(ch_port)
    settings.clickhouse_user = ch_user
    settings.clickhouse_password = ch_password
    settings.dbt_project_path = dbt_project_path
    settings.flags.ignore_permissions = True
    settings.save()
    # #142: the configurator sets the connection only after install-app, so a
    # fresh site's install-time reconcile ran against the default target
    # (localhost) and reached nothing. Fill the warehouse now that the real
    # target is known. An unchanged target (every redeploy of an existing
    # site) queues nothing: that deploy's bench migrate already reconciled.
    if _warehouse_target() != before:
        enqueue_reconcile_after_commit()
    frappe.db.commit()
    frappe.logger().info(f"EPM Settings configured: ClickHouse at {ch_host}:{ch_port}")


def _warehouse_target():
    """The ClickHouse connection EPM Settings points at, password included, so
    a rotated password counts as a new target."""
    from konsol.clickhouse import get_connection

    return {key: str(value) for key, value in get_connection().items()}


#: The worker job that fills the warehouse after an install, or after the
#: ClickHouse target changes (#142). It is a sync, not a build, so it does not
#: go through konsol.tasks.queue_consolidation_build.
RECONCILE_JOB = "konsol.install.reconcile_warehouse"


def after_sync():
    """install-app's last hook: queue a warehouse reconcile for after the commit.

    Without it a fresh site's write-through tables (Scenario, ISO Currency,
    Spread Profile and every other fixture-loaded doctype) stay empty until the
    first bench migrate (#142). after_migrate is the only other caller of
    reconcile_all, and nothing syncs during an install: sync_table and
    clickhouse.after_commit_once both stand down while frappe.flags.in_install
    is set.

    This is ``after_sync``, not ``after_install``. install-app runs
    after_install before it imports fixtures, and every fixture file commits,
    so a callback registered there would queue the job at the first fixture
    commit and a worker could publish half the reference data. after_sync runs
    after the fixtures and customizations, and Frappe calls it from install-app
    only, never from migrate.
    """
    enqueue_reconcile_after_commit()


def enqueue_reconcile_after_commit():
    """Queue ``reconcile_warehouse`` as a worker job once this transaction commits.

    The job runs in a fresh worker context with no install or migrate flag set,
    so sync_table writes, and it reads what the commit published.

    Not ``frappe.enqueue(..., enqueue_after_commit=True)``: Frappe runs
    after-commit callbacks unguarded, so a Redis outage there would raise out of
    install-app's final commit and fail the install over a step the next
    ``bench migrate`` repeats anyway.
    """

    def enqueue():
        try:
            frappe.enqueue(RECONCILE_JOB, queue="long")
        except Exception as e:  # noqa: BLE001 — never fail an install over it
            frappe.logger().warning("warehouse reconcile not queued", exc_info=True)
            print(
                f"konsol: warehouse reconcile not queued ({type(e).__name__}: {e}); "
                "the next bench migrate, or `bench execute konsol.clickhouse.reconcile_all`, fills it"
            )

    frappe.db.after_commit.add(enqueue)


#: How long one reconcile may hold the lock, and how long the next one waits
#: for it. Matches the long queue's job timeout.
RECONCILE_LOCK_SECONDS = 1500


def reconcile_warehouse():
    """Worker job: re-sync every write-through table through ``reconcile_all``.

    Runs are serialised per site by a Redis lock that waits. A fresh deploy
    queues two of these (after install, and after the configurator sets the
    ClickHouse target), and sync_table's TRUNCATE and INSERT are separate
    statements: two concurrent runs could interleave them (A truncates, B
    truncates, A inserts, B inserts) and double every row. The second run
    waits, then reconciles against the newest target. Not
    ``frappe.enqueue(job_id=..., deduplicate=True)``: that also drops a job
    whose twin has already *started*, i.e. the one carrying the real target.

    Best-effort like the migrate-time reconcile: a table that did not sync is
    logged, not raised. When no table synced at all (ClickHouse unreachable,
    wrong target) an Error Log says so, because the job itself still ends
    successfully. Returns reconcile_all's table -> row count map (None for a
    table that did not sync), or None when the lock was never acquired.
    """
    from konsol.clickhouse import reconcile_all

    lock = frappe.cache.lock(
        f"konsol:reconcile_warehouse:{frappe.local.site}",
        timeout=RECONCILE_LOCK_SECONDS,
        blocking_timeout=RECONCILE_LOCK_SECONDS,
    )
    if not lock.acquire():
        frappe.log_error(
            title="Warehouse reconcile skipped: another reconcile held the lock",
            message=f"Waited {RECONCILE_LOCK_SECONDS}s for konsol:reconcile_warehouse:"
            f"{frappe.local.site}. Run `bench execute konsol.clickhouse.reconcile_all`.",
        )
        return None
    try:
        synced = reconcile_all()
    finally:
        try:
            lock.release()
        except Exception:  # noqa: BLE001 — an expired lock is already gone
            frappe.logger().warning("reconcile job: lock release failed", exc_info=True)

    failed = sorted(table for table, rows in synced.items() if rows is None)
    frappe.logger().info(
        f"reconcile job: synced {len(synced) - len(failed)} of {len(synced)} write-through tables"
    )
    if failed:
        frappe.logger().warning(f"reconcile job: not synced: {', '.join(failed)}")
    if synced and len(failed) == len(synced):
        frappe.log_error(
            title="Warehouse reconcile: no table synced",
            message=f"None of the {len(synced)} write-through tables reached ClickHouse. "
            "Check the ClickHouse connection in EPM Settings, then run "
            "`bench execute konsol.clickhouse.reconcile_all`. On a fresh Docker deploy the "
            "install-time run meets the default localhost target; a second run follows "
            "once the configurator sets the real one.",
        )
    return synced


def after_migrate():
    """Called after bench migrate — ensures EPM roles exist, the dimension
    crosswalk seed reflects fixture-loaded Dimension Mapping docs, allocation
    config is synced to ClickHouse, and the Konsolidat desk workspace is present."""
    _restore_asset_manifest()
    create_roles()
    # F3: the three seed regenerators are gone — Dimension Mapping, Cash Flow
    # Category and Reporting Hierarchy write through to epm_staging like every
    # other governed table, and _reconcile_clickhouse() below re-syncs them
    # after fixture import (which does not fire on_update).
    _sync_allocation_config_to_clickhouse()
    # Anything seeded during migrate must come BEFORE the reconcile: sync_table
    # no-ops while frappe.flags.in_migrate is set, and reconcile_all
    # (force=True) is the one call that carries such rows through. Nothing is
    # seeded now; the demo ownership and annual budget that were are gone.
    _reconcile_clickhouse()
    _install_workflows()
    _ensure_indexes()
    _setup_dashboard()
    _retire_konsol_control_page()
    _sync_budget_line_custom_fields()


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


#: Every role konsol's permissions, workflows and budget layers name (F7,
#: 12 Sep 2026). The screen shows job titles; these names are what the code
#: checks. test_role_access asserts nothing the app names is missing here.
ROLES = {
    "EPM User": "Viewer: reads periods, reports and close status for their entities",
    "EPM Analyst": "Group Accountant: drafts adjustments, rates, ownership, intercompany and allocations; requests builds",
    "EPM Admin": "Close Lead: runs the close, approves builds and adjustments, signs off periods",
    "Entity Accountant": "Uploads and submits trial balances and fills in the base budget, for their own entities only",
    "Budget Submitter": "Base budget layer (kept as an alias; Entity Accountant is the new name)",
    "Budget Controller": "Challenge budget layer",
    "Budget Manager": "Management budget layer; locks the budget cycle",
    "Budget Approver": "Board budget layer",
}


def create_roles():
    """Create every role in ROLES that doesn't exist yet.

    Runs from after_install and after_migrate. Frappe usually creates these
    first, while syncing a doctype whose permission rows name them; this is
    the explicit list, so a role that no permission row names yet still
    exists. Existing roles are left exactly as the site has them. (Role has
    no description field, so ROLES's descriptions are the record of intent.)
    """
    for role_name in ROLES:
        if not frappe.db.exists("Role", role_name):
            role = frappe.new_doc("Role")
            role.role_name = role_name
            role.desk_access = 1
            role.insert(ignore_permissions=True)
            frappe.logger().info(f"Created role: {role_name}")
    frappe.db.commit()


def _ensure_indexes():
    """Indexes a doctype sync only adds when its JSON changes. Idempotent;
    never fails a migrate."""
    try:
        from konsol.consolidation.doctype.trial_balance_submission.trial_balance_submission import on_doctype_update
        on_doctype_update()
    except Exception:
        frappe.logger().warning("index setup skipped during migrate", exc_info=True)


def _install_workflows():
    """Create the app's workflows if missing; never overwrite a site's own.
    Never fails a migrate."""
    try:
        from konsol.workflows import install_workflows
        # Printed, not only logged: a role upgrade changes who may approve,
        # and the person running the migrate is the one who needs to know.
        for line in install_workflows():
            print(f"konsol workflows: {line}")
    except Exception as e:
        frappe.logger().warning("workflow install skipped during migrate", exc_info=True)
        print(f"konsol workflows: skipped ({type(e).__name__}: {e}); the next migrate tries again")


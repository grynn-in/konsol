"""
Post-install setup for Konsolidat.
Called by the Docker configurator to set up EPM Settings with ClickHouse connection.
Also creates EPM roles on migrate.
"""
import frappe


def setup_epm_settings(ch_host="localhost", ch_port=8123, ch_user="default", ch_password="open_epm_dev"):
    """Configure EPM Settings with ClickHouse connection details."""
    if not frappe.db.exists("DocType", "EPM Settings"):
        frappe.logger().warning("EPM Settings doctype not found. Skipping setup.")
        return

    settings = frappe.get_single("EPM Settings")
    settings.clickhouse_host = ch_host
    settings.clickhouse_port = int(ch_port)
    settings.clickhouse_user = ch_user
    settings.clickhouse_password = ch_password
    settings.flags.ignore_permissions = True
    settings.save()
    frappe.db.commit()
    frappe.logger().info(f"EPM Settings configured: ClickHouse at {ch_host}:{ch_port}")


def after_migrate():
    """Called after bench migrate — ensures EPM roles exist and the dimension
    crosswalk seed reflects fixture-loaded Dimension Mapping docs."""
    _create_roles()
    _regenerate_dimension_mappings_seed()


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

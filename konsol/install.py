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
    """Called after bench migrate — ensures EPM roles exist."""
    _create_roles()


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

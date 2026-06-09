"""
Post-install setup for Konsolidat.
Called by the Docker configurator to set up EPM Settings with ClickHouse connection.
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
	settings.save(ignore_permissions=True)
	frappe.db.commit()
	frappe.logger().info(f"EPM Settings configured: ClickHouse at {ch_host}:{ch_port}")

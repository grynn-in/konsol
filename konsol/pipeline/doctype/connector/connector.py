"""Connector — registry of live ERP connector instances.

Single source of truth for which ERP adapters run: each enabled Connector
contributes its erp_type to dbt_project.yml vars.erp_sources (regenerated on
save/delete, mirroring how Dimension/Measure drive vars). Also records the
legal entities served, per-connector dimension column mappings, and sync
status used by Build Governance preflight.
"""
import frappe
from frappe.model.document import Document

from konsol.connector_credentials import build_extract_config, build_writeback_config
from konsol.dbt_config import regenerate_vars

# Informational source label per ERP type (from the roadmap connector table).
_AIRBYTE_SOURCE = {
    "d365_fo": "Airbyte D365 F&O (OData)",
    "d365_bc": "Airbyte D365 BC (REST/OData)",
    "sap_s4": "Airbyte SAP S/4HANA (OData)",
    "sap_ecc": "Airbyte SAP ECC (RFC/IDoc)",
    "sap_b1": "Airbyte SAP B1 (Service Layer)",
    "erpnext": "Airbyte ERPNext (Frappe REST)",
}


class Connector(Document):

    def validate(self):
        # Derived, read-only helpers kept in sync with erp_type.
        self.dbt_adapter_prefix = f"stg_{self.erp_type}" if self.erp_type else ""
        self.airbyte_source = _AIRBYTE_SOURCE.get(self.erp_type, "")

    def on_update(self):
        # Enabled set / erp_type may have changed → refresh erp_sources.
        regenerate_vars()

    def after_delete(self):
        # Refresh erp_sources AFTER the row is gone. on_trash runs *before* the
        # DB delete (delete_doc.py: on_trash → delete → after_delete), so
        # regenerating there would still see this connector and leave its
        # erp_type in erp_sources.
        regenerate_vars()

    def get_extract_config(self):
        """Airbyte-source-shaped extract profile for this connector."""
        return build_extract_config(self)

    def get_writeback_config(self):
        """Runtime write-back profile for this connector."""
        return build_writeback_config(self)

    @frappe.whitelist()
    def test_extract_connection(self):
        """Validate extract credentials against the live ERP."""
        from konsol.extract_check import check_extract_connection

        extract_config = build_extract_config(self)
        ok, message = check_extract_connection(extract_config, self.erp_type)
        return {
            "ok": ok,
            "message": message,
            "connector_name": self.connector_name,
        }

    @frappe.whitelist()
    def test_writeback_connection(self):
        """Validate write-back credentials against the live ERP."""
        from konsol.extract_check import check_writeback_connection

        writeback_config = build_writeback_config(self)
        ok, message = check_writeback_connection(writeback_config, self.erp_type)
        return {
            "ok": ok,
            "message": message,
            "connector_name": self.connector_name,
        }

    @frappe.whitelist()
    def provision_airbyte(self):
        """Test extract creds, then upsert Airbyte source + connection."""
        from konsol.airbyte_service import provision_connector_airbyte

        return provision_connector_airbyte(self.name)
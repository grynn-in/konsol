"""Connector — registry of live ERP connector instances.

Single source of truth for which ERP adapters run: each enabled Connector
contributes its erp_type to dbt_project.yml vars.erp_sources (regenerated on
save/delete, mirroring how Dimension/Measure drive vars). Also records the
legal entities served, per-connector dimension column mappings, and sync
status used by Build Governance preflight.
"""
import frappe
from frappe.model.document import Document

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

    def on_trash(self):
        regenerate_vars()

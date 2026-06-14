"""ERP Source — config doctype listing the ERP systems dbt should stage.

Each enabled record is emitted into the dbt_project.yml `erp_sources` var by
konsol.dbt_config.regenerate_vars(), so the staging layer knows which ERP
connectors feed it. Replaces the previously hand-maintained `erp_sources` list
(which the regenerator could clobber).
"""
import re

import frappe
from frappe.model.document import Document

# erp_sources values become dbt var entries / staging folder names — restrict
# to lowercase identifiers so they are safe to interpolate into dbt.
_SAFE_SOURCE_NAME = re.compile(r"^[a-z][a-z0-9_]*$")


class ERPSource(Document):

    def validate(self):
        if not _SAFE_SOURCE_NAME.match(self.source_name or ""):
            frappe.throw(
                f"Source Name '{self.source_name}' must be a lowercase "
                f"identifier (letters, digits, underscore; starting with a "
                f"letter), e.g. d365_fo or erpnext.",
                frappe.ValidationError,
            )

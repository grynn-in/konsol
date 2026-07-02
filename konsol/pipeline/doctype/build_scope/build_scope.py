"""Build Scope — the set of Build Governance domains (single source of truth).

Each domain maps to a dbt selector tag:domain:<domain_name> and a flag for
whether a build of it requires epm_raw data. konsol.tasks reads these at runtime
(falling back to its hardcoded defaults when the doctype is empty), and Gold
Model.build_domain links here.
"""
import re

import frappe
from frappe.model.document import Document

# domain_name is interpolated into a dbt tag selector, so keep it a safe
# lowercase identifier.
_SAFE_DOMAIN = re.compile(r"^[a-z][a-z0-9_]*$")


class BuildScope(Document):

    def validate(self):
        if not _SAFE_DOMAIN.match(self.domain_name or ""):
            frappe.throw(
                f"Domain Name '{self.domain_name}' must be a lowercase identifier "
                f"(letters, digits, underscore; starting with a letter), e.g. actuals.",
                frappe.ValidationError,
            )
        if self.domain_name == "full":
            frappe.throw(
                "'full' is a reserved build scope (full rebuild), not a per-model "
                "domain — it cannot be a Build Scope.",
                frappe.ValidationError,
            )

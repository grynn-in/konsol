"""Fact Table — registry of ClickHouse fact tables for dynamic schema."""
import json

import frappe
from frappe.model.document import Document


class FactTable(Document):
    def validate(self):
        self._validate_json_fields()

    def _validate_json_fields(self):
        """Ensure measures/dimensions are valid JSON arrays."""
        for field in ("measures", "dimensions"):
            val = self.get(field)
            if val:
                try:
                    parsed = json.loads(val)
                except (json.JSONDecodeError, TypeError):
                    frappe.throw(f"{field} must be valid JSON")
                if not isinstance(parsed, list):
                    frappe.throw(f"{field} must be a JSON array")

"""Fact Table — registry of ClickHouse fact tables for dynamic schema."""
import json

import frappe
from frappe.model.document import Document


class FactTable(Document):
    def validate(self):
        self._validate_json_fields()
        self._sync_dimensions_json()

    def _validate_json_fields(self):
        """Ensure measures is a valid JSON array."""
        val = self.get("measures")
        if val:
            try:
                parsed = json.loads(val)
            except (json.JSONDecodeError, TypeError):
                frappe.throw("measures must be valid JSON")
            if not isinstance(parsed, list):
                frappe.throw("measures must be a JSON array")

    def _sync_dimensions_json(self):
        """Auto-populate dimensions JSON from fact_dimensions child table.

        Keeps the hidden JSON field in sync so existing consumers
        (api.py, schema_apply.py) work without changes.
        """
        if self.fact_dimensions:
            dim_names = [row.dimension for row in self.fact_dimensions]
            self.dimensions = json.dumps(sorted(dim_names))
        elif not self.dimensions:
            self.dimensions = "[]"

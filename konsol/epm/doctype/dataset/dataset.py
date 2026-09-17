"""Dataset — registry of queryable ClickHouse datasets for dynamic schema.

Saves validate measures/dimensions against the Published registries and keep
the hidden `measures`/`dimensions` JSON fields in sync from the child tables so
existing consumers (api.py, schema_apply.py) work unchanged. Use Publish /
Unpublish to apply schema (ClickHouse DDL + dbt source) and trigger a rebuild.
"""
import json
import re

import frappe
from frappe.model.document import Document

from konsol.schema_lifecycle import apply_and_rebuild, check_epm_admin

_SAFE_IDENTIFIER = re.compile(r"^[a-z][a-z0-9_]*$")
_SAFE_TABLE_NAME = re.compile(r"^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$")


class Dataset(Document):
    def validate(self):
        self._validate_table_name()
        self._validate_measures()
        self._validate_default_measure()
        self._validate_dimensions()
        self._validate_extra_columns()
        self._sync_json_fields()

    def _validate_table_name(self):
        if not _SAFE_TABLE_NAME.match(self.clickhouse_table or ""):
            frappe.throw(
                f"ClickHouse Table '{self.clickhouse_table}' must be "
                f"schema.table in lowercase, e.g. epm_gold.gold_trial_balance"
            )

    def _validate_measures(self):
        """Every fact_measures row must reference a Published Measure.

        Skipped during fixture import / migrate / install: fixtures load in
        alphabetical filename order (dataset.json before measure.json), so
        the referenced measures may not be Published yet. Integrity is still
        enforced on normal saves and on publish().
        """
        if frappe.flags.in_install or frappe.flags.in_migrate or frappe.flags.in_import:
            return
        for row in self.fact_measures or []:
            status = frappe.db.get_value("Measure", row.measure, "status")
            if status != "Published":
                frappe.throw(
                    f"Measure '{row.measure}' is not Published "
                    f"(status={status or 'missing'})"
                )

    def _validate_default_measure(self):
        """The Default Measure — what a read of this dataset gets when it names
        no measure (konsol#105 Decision 1) — must be one of this dataset's own
        measures. Blank is allowed: such a read is then answered with an error
        naming this dataset, never with a guess.

        NOT skipped during fixture import / migrate / install, unlike
        _validate_measures: this reads only this document's own child rows, so
        there is no load-order problem, and a re-import must not be able to
        install a default that is not in the dataset it belongs to.
        """
        if not self.default_measure:
            return
        available = [row.measure for row in self.fact_measures or []]
        if self.default_measure not in available:
            frappe.throw(
                f"Default Measure '{self.default_measure}' is not a measure of "
                f"dataset '{self.name or self.fact_name}'. Its measures are: "
                f"{', '.join(sorted(available)) or '(none)'}"
            )

    def _validate_dimensions(self):
        """Every fact_dimensions row must reference a Published Dimension.

        Skipped during fixture import / migrate / install (see _validate_measures).
        """
        if frappe.flags.in_install or frappe.flags.in_migrate or frappe.flags.in_import:
            return
        for row in self.fact_dimensions or []:
            status = frappe.db.get_value("Dimension", row.dimension, "status")
            if status != "Published":
                frappe.throw(
                    f"Dimension '{row.dimension}' is not Published "
                    f"(status={status or 'missing'})"
                )

    def _validate_extra_columns(self):
        for col in self._parse_extra_columns():
            name = col.get("name", "")
            if not _SAFE_IDENTIFIER.match(name):
                frappe.throw(
                    f"extra_columns name '{name}' must be a lowercase identifier"
                )

    def _parse_extra_columns(self):
        val = self.get("extra_columns")
        if not val:
            return []
        try:
            parsed = json.loads(val)
        except (json.JSONDecodeError, TypeError):
            frappe.throw("extra_columns must be valid JSON")
        if not isinstance(parsed, list):
            frappe.throw("extra_columns must be a JSON array of {name, ch_type}")
        return parsed

    def _sync_json_fields(self):
        """Mirror the child tables into the hidden JSON fields read by api.py /
        schema_apply.py. Only overwrite when the child table is populated so a
        legacy record carrying raw JSON (no child rows yet) is left intact."""
        if self.fact_measures:
            self.measures = json.dumps(sorted(r.measure for r in self.fact_measures))
        elif not self.measures:
            self.measures = "[]"

        if self.fact_dimensions:
            self.dimensions = json.dumps(
                sorted(r.dimension for r in self.fact_dimensions)
            )
        elif not self.dimensions:
            self.dimensions = "[]"

    @frappe.whitelist()
    def publish(self):
        """Publish this fact: apply schema (DDL + dbt source) + trigger rebuild."""
        check_epm_admin()
        self.status = "Published"
        self.save()
        apply_and_rebuild(self, "Publish")

    @frappe.whitelist()
    def unpublish(self):
        """Unpublish (deactivate) this fact. Never drops the ClickHouse table —
        physical teardown stays a deliberate manual admin action."""
        check_epm_admin()
        self.status = "Inactive"
        self.save()
        apply_and_rebuild(self, "Unpublish")

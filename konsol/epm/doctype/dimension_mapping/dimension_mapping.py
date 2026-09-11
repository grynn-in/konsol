"""Dimension Mapping — crosswalk from a raw ERP dimension value to a canonical one.

Saves are pure metadata. Use Publish/Unpublish to (re)generate the
epm_staging.dimension_mappings crosswalk consumed by the dbt dim_harmonize()
macro and request a governed rebuild. Keyed on (dimension, erp_source, entity,
source_value), which must be unique among non-Inactive rows.
"""
import frappe

from konsol.governed_reference import GovernedReferenceDocument


class DimensionMapping(GovernedReferenceDocument):
    # F3: write-through to the warehouse — the crosswalk USED to be written as
    # a CSV seed into the dbt repo on every save and migrate. Only Published
    # rows sync (dbt filtered on status='Published' already); TRUNCATE+INSERT,
    # reconciled after migrate like every other write-through table. The
    # publish/unpublish/after_delete sync itself lives in the base class, which
    # reconcile_all reads through CH_SYNC_FILTERS — the two paths used to
    # disagree about Draft and Inactive rows.
    CH_TABLE = "epm_staging.dimension_mappings"
    CH_SYNC_FILTERS = {"status": "Published"}
    CH_FIELD_MAP = {
        "dimension": "dimension",
        "erp_source": "erp_source",
        "entity": "entity",
        "source_value": "source_value",
        "canonical_value": "canonical_value",
        "canonical_label": "canonical_label",
        "status": "status",
    }

    def validate(self):
        self._validate_unique_key()

    def _validate_unique_key(self):
        """(dimension, erp_source, entity, source_value) maps to ONE canonical value.

        Enforced against other non-Inactive rows so a source value never has two
        live crosswalk targets for the same ERP and entity. Blank entity is the
        ERP-wide default; an entity-specific row may coexist with it and takes
        precedence in the warehouse (konsol #111).

        ``entity`` is a Link, and a blank Link is stored as NULL, not '' — so
        matching it with ``self.entity or ""`` compared NULL to '' and found
        nothing. The guard was inert for exactly the rows that matter most, the
        ERP-wide defaults: two of them both synced (both rendered as '' in
        ClickHouse) and the _dflt join in dim_harmonize fanned every fact row
        out. Match both spellings of "blank".
        """
        entity = self.entity or ""
        dupe = frappe.db.exists(
            "Dimension Mapping",
            {
                "dimension": self.dimension,
                "erp_source": self.erp_source,
                "entity": entity if entity else ["in", ["", None]],
                "source_value": self.source_value,
                "status": ["!=", "Inactive"],
                "name": ["!=", self.name],
            },
        )
        if dupe:
            scope = f"entity '{entity}'" if entity else "all entities"
            frappe.throw(
                f"A mapping for {self.dimension} / {self.erp_source} / "
                f"'{self.source_value}' ({scope}) already exists ({dupe})."
            )

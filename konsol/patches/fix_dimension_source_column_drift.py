"""Realign Dimension.source_column to the harmonized canonical name.

Old fixtures seeded Dimension.source_column with the RAW ERP column name
(CostCenter, Department, BusinessUnit). Bronze models consume the *canonical*
staging layer, where those columns are already harmonized to
dim_cost_center / dim_department / dim_business_unit, so `dim_select_from_source`
emitted references to columns that don't exist and `regenerate_vars()` wrote a
`dbt_project.yml` that failed the dbt build with ClickHouse UNKNOWN_IDENTIFIER
(code 47).

Fixtures only reliably seed *new* sites; existing MariaDB rows persist, so this
patch realigns them. Idempotent.

(The companion defect — base_measures being built from every published Measure
instead of the trial-balance fact's measures — is fixed structurally in
konsol/dbt_config.py and needs no data migration.)
"""
import frappe

# source_column must equal the harmonized name (= dimension_name), because
# bronze reads the canonical staging layer, not raw ERP columns.
_DIM_SOURCE_COLUMNS = {
    "dim_cost_center": "dim_cost_center",
    "dim_department": "dim_department",
    "dim_business_unit": "dim_business_unit",
}


def execute():
    if not frappe.db.table_exists("Dimension"):
        return

    for name, source_column in _DIM_SOURCE_COLUMNS.items():
        if not frappe.db.exists("Dimension", name):
            continue
        if frappe.db.get_value("Dimension", name, "source_column") != source_column:
            frappe.db.set_value("Dimension", name, "source_column", source_column)

    frappe.db.commit()

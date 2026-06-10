"""Apply Schema — single deliberate action to regenerate all dynamic schema.

Reads all config doctypes (Dimension, Measure, Fact Table) and applies:
  1. dbt_project.yml vars regeneration
  2. ClickHouse ALTER TABLE for missing columns
  3. Budget Input custom field sync
  4. Optional dbt build trigger

Called deliberately — NOT triggered automatically on individual saves.
"""
import json
import re

import frappe

from konsol.clickhouse import execute as ch_execute, get_connection
from konsol.dbt_config import regenerate_vars

_SAFE_IDENTIFIER = re.compile(r"^[a-z][a-z0-9_]*$")

# ClickHouse type mapping for Cube types
_CH_TYPE_MAP = {
    "string": "String",
    "number": "Float64",
}


@frappe.whitelist()
def apply_schema(run_dbt=False):
    """Read all config doctypes, regenerate everything in one shot.

    Args:
        run_dbt: If True, enqueue a background dbt build after schema changes.

    Returns:
        Summary dict of what was applied.
    """
    summary = {
        "vars_updated": False,
        "columns_added": [],
        "budget_fields_synced": [],
        "dbt_triggered": False,
        "errors": [],
    }

    # 1. Regenerate dbt_project.yml vars
    try:
        regenerate_vars()
        summary["vars_updated"] = True
    except Exception as e:
        summary["errors"].append(f"dbt vars: {str(e)}")
        frappe.log_error("schema_apply: dbt vars failed", frappe.get_traceback())

    # 2. ClickHouse ALTER TABLE for missing dimension columns
    try:
        summary["columns_added"] = _apply_clickhouse_columns()
    except Exception as e:
        summary["errors"].append(f"ClickHouse DDL: {str(e)}")
        frappe.log_error("schema_apply: CH DDL failed", frappe.get_traceback())

    # 3. Sync Budget Input custom fields
    try:
        summary["budget_fields_synced"] = _sync_budget_custom_fields()
    except Exception as e:
        summary["errors"].append(f"Budget fields: {str(e)}")
        frappe.log_error("schema_apply: budget fields failed", frappe.get_traceback())

    # 4. Optional dbt build
    if run_dbt or frappe.form_dict.get("run_dbt"):
        try:
            frappe.enqueue(
                "konsol.tasks.run_dbt_build",
                queue="long",
                timeout=600,
            )
            summary["dbt_triggered"] = True
        except Exception as e:
            summary["errors"].append(f"dbt trigger: {str(e)}")

    return summary


def _apply_clickhouse_columns():
    """For each dimension, ensure column exists on all relevant fact tables.

    Returns list of columns added (as "table.column" strings).
    """
    dimensions = frappe.get_all(
        "Dimension",
        fields=["dimension_name", "cube_type"],
        limit_page_length=0,
    )
    fact_tables = frappe.get_all(
        "Fact Table",
        fields=["fact_name", "clickhouse_table", "dimensions"],
        limit_page_length=0,
    )

    added = []
    for fact in fact_tables:
        fact_dims = set(json.loads(fact.dimensions or "[]"))
        for dim in dimensions:
            if dim.dimension_name not in fact_dims:
                continue
            if not _SAFE_IDENTIFIER.match(dim.dimension_name):
                continue

            ch_type = _CH_TYPE_MAP.get(dim.cube_type or "string", "String")
            default = "''" if ch_type == "String" else "0"
            sql = (
                f"ALTER TABLE {fact.clickhouse_table} "
                f"ADD COLUMN IF NOT EXISTS {dim.dimension_name} {ch_type} "
                f"DEFAULT {default}"
            )
            try:
                ch_execute(sql)
                added.append(f"{fact.clickhouse_table}.{dim.dimension_name}")
            except Exception as e:
                # Log but don't fail the whole operation
                frappe.log_error(
                    f"schema_apply: ALTER TABLE failed for "
                    f"{fact.clickhouse_table}.{dim.dimension_name}",
                    str(e),
                )

    return added


def _sync_budget_custom_fields():
    """Ensure Budget Input has Custom Fields for all in_budget dimensions.

    Adds missing fields, removes orphaned ones.
    Returns list of field actions taken.
    """
    budget_dims = frappe.get_all(
        "Dimension",
        filters={"in_budget": 1},
        fields=["dimension_name", "label"],
        limit_page_length=0,
    )
    wanted = {d.dimension_name for d in budget_dims}
    label_map = {d.dimension_name: d.label for d in budget_dims}

    # Get existing custom fields for Budget Input that are dimension fields
    existing = frappe.get_all(
        "Custom Field",
        filters={
            "dt": "Budget Input",
            "fieldname": ("like", "dim_%"),
        },
        fields=["name", "fieldname"],
        limit_page_length=0,
    )
    existing_names = {cf.fieldname for cf in existing}

    actions = []

    # Add missing
    for dim_name in sorted(wanted - existing_names):
        cf = frappe.new_doc("Custom Field")
        cf.dt = "Budget Input"
        cf.fieldname = dim_name
        cf.fieldtype = "Data"
        cf.label = label_map.get(dim_name, dim_name)
        cf.insert_after = "main_account"
        cf.insert()
        actions.append(f"added {dim_name}")

    # Remove orphaned
    for cf in existing:
        if cf.fieldname not in wanted:
            frappe.delete_doc("Custom Field", cf.name)
            actions.append(f"removed {cf.fieldname}")

    return actions

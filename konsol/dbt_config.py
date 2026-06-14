"""dbt_project.yml vars regenerator for EPM config doctypes.

Reads Dimension, Measure, and Fiscal Period docs from Frappe and
regenerates the vars section of dbt_project.yml, preserving all
non-vars sections (models, seeds, paths, etc.).
"""
import frappe
import yaml


def _merge_vars_into_yaml(original, new_vars):
    """Merge new vars into original YAML dict, preserving non-vars keys.

    Args:
        original: Full parsed dbt_project.yml dict.
        new_vars: Dict of vars to set.

    Returns:
        Updated dict with vars replaced but everything else preserved.
    """
    result = dict(original)
    result["vars"] = new_vars
    return result


def _get_dbt_project_base():
    """Get the dbt project base dir from EPM Settings."""
    settings = frappe.get_single("EPM Settings")
    return settings.dbt_project_path or "/home/pd/open_epm/dbt_project"


def _get_dbt_project_path():
    """Get dbt_project.yml path from EPM Settings."""
    return f"{_get_dbt_project_base()}/dbt_project.yml"


# Header for the dimension_mappings crosswalk seed (must match the columns the
# dbt dim_harmonize() macro + dimension_mappings.csv expect — see konsolidat).
_DIM_MAPPING_COLUMNS = [
    "dimension", "erp_source", "source_value", "canonical_value",
    "canonical_label", "status",
]


def regenerate_dimension_mappings_seed():
    """Regenerate seeds/dimension_mappings.csv from published Dimension Mapping docs.

    Frappe is the source of truth for the crosswalk (mirrors how regenerate_vars
    owns dbt_project.yml vars). Only Published rows are written; the dbt side
    already filters on status='Published', and an empty file (header only) is
    valid — it just means "no mappings, everything passes through".

    Returns the seed path written, or None if the dbt project dir is absent
    (e.g. dbt on another host) — caller treats that as a skip.
    """
    import csv
    import os

    base = _get_dbt_project_base()
    seeds_dir = os.path.join(base, "seeds")
    if not os.path.isdir(seeds_dir):
        frappe.logger().warning(
            f"dbt seeds dir not found at {seeds_dir} — skipping dimension_mappings "
            f"regeneration. Set dbt_project_path in EPM Settings if dbt is remote."
        )
        return None

    rows = frappe.get_all(
        "Dimension Mapping",
        filters={"status": "Published"},
        fields=["dimension", "erp_source", "source_value", "canonical_value",
                "canonical_label"],
        order_by="dimension asc, erp_source asc, source_value asc",
        limit_page_length=0,
    )

    path = os.path.join(seeds_dir, "dimension_mappings.csv")
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=_DIM_MAPPING_COLUMNS)
        writer.writeheader()
        for r in rows:
            writer.writerow({
                "dimension": r.dimension or "",
                "erp_source": r.erp_source or "",
                "source_value": r.source_value or "",
                "canonical_value": r.canonical_value or "",
                "canonical_label": r.canonical_label or "",
                "status": "Published",
            })
    return path


def _build_dimensions_vars():
    """Build dimensions list from Dimension doctype."""
    docs = frappe.get_all(
        "Dimension",
        filters={"status": "Published"},
        fields=["dimension_name", "source_column", "label", "cube_type",
                "in_budget", "allocation_role"],
        order_by="dimension_name asc",
        limit_page_length=0,
    )
    dimensions = []
    for d in docs:
        dim = {
            "name": d.dimension_name,
            "source_column": d.source_column,
            "label": d.label,
            "cube_type": d.cube_type or "string",
            "in_budget": bool(d.in_budget),
        }
        if d.allocation_role:
            dim["allocation_role"] = d.allocation_role
        dimensions.append(dim)
    return dimensions


def _build_measures_vars():
    """Build base_measures list from Measure doctype."""
    docs = frappe.get_all(
        "Measure",
        filters={"status": "Published"},
        fields=["measure_name", "expression", "label", "cube_type"],
        order_by="measure_name asc",
        limit_page_length=0,
    )
    return [
        {
            "name": d.measure_name,
            "expression": d.expression,
            "label": d.label,
            "cube_type": d.cube_type or "sum",
        }
        for d in docs
    ]


def _build_fiscal_vars():
    """Build fiscal vars from Fiscal Period doctype.

    Returns dict with:
        - fiscal_extra_periods: list for periods 0, 13, 14
        - fiscal_quarter_mapping: dict {period: quarter} for 1-12
        - fiscal_half_mapping: dict {period: half} for 1-12
    """
    docs = frappe.get_all(
        "Fiscal Period",
        fields=["fiscal_period", "label", "quarter", "half"],
        order_by="fiscal_period asc",
        limit_page_length=0,
    )

    extra_periods = []
    quarter_mapping = {}
    half_mapping = {}

    for d in docs:
        p = int(d.fiscal_period)
        if p < 1 or p > 12:
            extra_periods.append({
                "period": p,
                "label": d.label,
                "quarter": d.quarter,
                "half": d.half,
            })
        else:
            quarter_mapping[p] = d.quarter
            half_mapping[p] = d.half

    result = {}
    if extra_periods:
        result["fiscal_extra_periods"] = extra_periods
    if quarter_mapping:
        result["fiscal_quarter_mapping"] = quarter_mapping
    if half_mapping:
        result["fiscal_half_mapping"] = half_mapping
    return result


def _build_erp_sources_vars():
    """Build erp_sources list from enabled Connector docs.

    Returns the distinct erp_type values of enabled connectors, stable-ordered.
    Two connectors of the same erp_type (e.g. two SAP tenants) collapse to one
    erp_source — erp_source is per-ERP-product, not per-tenant. Returns [] when
    there are no enabled connectors (caller preserves the existing value).
    """
    if not frappe.db.table_exists("Connector"):
        return []
    docs = frappe.get_all(
        "Connector",
        filters={"enabled": 1},
        fields=["erp_type"],
        order_by="erp_type asc",
        limit_page_length=0,
    )
    seen = []
    for d in docs:
        if d.erp_type and d.erp_type not in seen:
            seen.append(d.erp_type)
    return seen


def regenerate_vars():
    """Regenerate the vars section of dbt_project.yml from Frappe doctypes.

    Reads Dimension, Measure, and Fiscal Period docs, builds the vars
    dict, and writes it back to dbt_project.yml while preserving all
    other sections.
    """
    path = _get_dbt_project_path()

    try:
        with open(path) as f:
            original = yaml.safe_load(f)
    except FileNotFoundError:
        frappe.logger().warning(
            f"dbt_project.yml not found at {path} — skipping vars regeneration. "
            f"Set dbt_project_path in EPM Settings if dbt is on a different host."
        )
        return

    # Start with existing vars to preserve any manual entries
    new_vars = {}

    # Build from doctypes
    dimensions = _build_dimensions_vars()
    if dimensions:
        new_vars["dimensions"] = dimensions

    measures = _build_measures_vars()
    if measures:
        new_vars["base_measures"] = measures

    fiscal = _build_fiscal_vars()
    new_vars.update(fiscal)

    # erp_sources from enabled connectors; preserve the existing value (default
    # ['d365_fo']) when no connectors are registered yet so we never wipe it.
    erp_sources = _build_erp_sources_vars()
    if not erp_sources:
        erp_sources = (original.get("vars") or {}).get("erp_sources") or ["d365_fo"]
    new_vars["erp_sources"] = erp_sources

    # Merge and write
    updated = _merge_vars_into_yaml(original, new_vars)

    with open(path, "w") as f:
        yaml.dump(updated, f, default_flow_style=False, sort_keys=False,
                  allow_unicode=True)

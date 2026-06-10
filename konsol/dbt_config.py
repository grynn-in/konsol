"""dbt_project.yml vars regenerator for EPM config doctypes.

Reads Dimension, Measure, and Fiscal Period docs from Frappe and
regenerates the vars section of dbt_project.yml, preserving all
non-vars sections (models, seeds, paths, etc.).
"""
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


def _get_dbt_project_path():
    """Get dbt_project.yml path from EPM Settings."""
    import frappe
    settings = frappe.get_single("EPM Settings")
    base = settings.dbt_project_path or "/home/pd/open_epm/dbt_project"
    return f"{base}/dbt_project.yml"


def _build_dimensions_vars():
    """Build dimensions list from Dimension doctype."""
    import frappe
    docs = frappe.get_all(
        "Dimension",
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
    import frappe
    docs = frappe.get_all(
        "Measure",
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
    import frappe
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
        import frappe
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

    # Merge and write
    updated = _merge_vars_into_yaml(original, new_vars)

    with open(path, "w") as f:
        yaml.dump(updated, f, default_flow_style=False, sort_keys=False,
                  allow_unicode=True)

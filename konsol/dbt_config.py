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

# Header for the cash_flow_categories seed (must match seeds/cash_flow_categories.csv
# + gold_cash_flow_indirect / gold_consolidated_cash_flow — see konsolidat#63).
_CASH_FLOW_CATEGORY_COLUMNS = [
    "main_account", "cf_category", "cf_line_item", "is_cash", "sign",
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


def regenerate_cash_flow_categories_seed():
    """Regenerate seeds/cash_flow_categories.csv from Published Cash Flow Category docs.

    Frappe is the source of truth for the BS-account → cash-flow-line crosswalk
    (mirrors regenerate_dimension_mappings_seed). Only Published rows are written;
    a header-only file is valid (no mappings). Returns the seed path, or None if
    the dbt project dir is absent (dbt on another host) — caller treats as skip.
    """
    import csv
    import os

    base = _get_dbt_project_base()
    seeds_dir = os.path.join(base, "seeds")
    if not os.path.isdir(seeds_dir):
        frappe.logger().warning(
            f"dbt seeds dir not found at {seeds_dir} — skipping cash_flow_categories "
            f"regeneration. Set dbt_project_path in EPM Settings if dbt is remote."
        )
        return None

    rows = frappe.get_all(
        "Cash Flow Category",
        filters={"status": "Published"},
        fields=["main_account", "cf_category", "cf_line_item", "is_cash", "sign"],
        order_by="main_account asc",
        limit_page_length=0,
    )

    path = os.path.join(seeds_dir, "cash_flow_categories.csv")
    with open(path, "w", newline="") as f:
        # LF line terminator (csv default is CRLF) so a regenerate stays
        # byte-identical to the committed LF seed — avoids spurious diffs.
        writer = csv.DictWriter(
            f, fieldnames=_CASH_FLOW_CATEGORY_COLUMNS, lineterminator="\n")
        writer.writeheader()
        for r in rows:
            writer.writerow({
                "main_account": r.main_account or "",
                "cf_category": r.cf_category or "",
                "cf_line_item": r.cf_line_item or "",
                "is_cash": int(r.is_cash or 0),
                "sign": int(r.sign or 1),
            })
    return path


# Must match reporting_hierarchies.csv / gold_reporting_hierarchy (konsolidat).
_REPORTING_HIERARCHY_COLUMNS = [
    "hierarchy_name", "dimension", "member_code", "member_label",
    "parent_member_code", "is_group", "hierarchy_level", "path",
    "effective_from", "effective_to", "is_default", "status",
]


def regenerate_reporting_hierarchies_seed():
    """Regenerate seeds/reporting_hierarchies.csv from Published hierarchies."""
    import csv
    import os

    from konsol.reporting_hierarchy_seed import flatten_reporting_hierarchies

    base = _get_dbt_project_base()
    seeds_dir = os.path.join(base, "seeds")
    if not os.path.isdir(seeds_dir):
        frappe.logger().warning(
            f"dbt seeds dir not found at {seeds_dir} — skipping "
            f"reporting_hierarchies regeneration."
        )
        return None

    rows = flatten_reporting_hierarchies(frappe)
    path = os.path.join(seeds_dir, "reporting_hierarchies.csv")
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=_REPORTING_HIERARCHY_COLUMNS)
        writer.writeheader()
        for r in rows:
            writer.writerow(r)
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


# Fact whose measures define `base_measures` (the GL trial-balance grain).
_TRIAL_BALANCE_DBT_MODEL = "gold_trial_balance"

# Last-resort GL-grain measures if the trial-balance fact is unconfigured. These
# are the only ones computable from silver_gl_entries; keeps the dbt build valid.
_DEFAULT_BASE_MEASURE_NAMES = (
    "period_debit",
    "period_credit",
    "period_net_amount",
    "transaction_count",
)


def _build_measures_vars():
    """Build base_measures for the GL trial-balance grain.

    `base_measures` feeds `measure_select()` in `gold_trial_balance`, which emits
    each measure's `expression` directly over `silver_gl_entries`. So it must be
    exactly the measures of the trial-balance fact (the Dataset that produces
    `gold_trial_balance`) — NOT every published Measure. Measures belonging to
    other facts (budget/variance/driver grains) reference columns absent from
    `silver_gl_entries` and would break the dbt build (UNKNOWN_IDENTIFIER) if
    emitted here. The fact registry is the single source of truth for which
    measures live at this grain.
    """
    measure_names = _trial_balance_measure_names()

    filters = {"status": "Published"}
    if measure_names:
        filters["measure_name"] = ["in", measure_names]

    docs = frappe.get_all(
        "Measure",
        filters=filters,
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


def _trial_balance_measure_names():
    """Names of the measures actually computed in the trial-balance dbt model.

    These are the GL-grain measures of the published trial-balance fact, MINUS
    any measure that fact reroutes to a different table. A rerouted measure is
    served from elsewhere at query time (e.g. ytd_net_amount is read from
    gold_balance_sheet.cumulative_balance, a column absent from
    silver_gl_entries) and is NOT computable in gold_trial_balance, so it must
    not enter base_measures. Falls back to the safe default set when no
    trial-balance fact is configured (so gold_trial_balance always keeps its
    required aggregates and the build stays valid).
    """
    if not frappe.db.table_exists("Dataset"):
        return list(_DEFAULT_BASE_MEASURE_NAMES)

    fact = frappe.get_all(
        "Dataset",
        filters={"dbt_model": _TRIAL_BALANCE_DBT_MODEL, "status": "Published"},
        fields=["name", "reroute_measure"],
        limit_page_length=1,
    )
    if not fact:
        return list(_DEFAULT_BASE_MEASURE_NAMES)

    names = frappe.get_all(
        "Dataset Measure",
        filters={"parent": fact[0].name, "parenttype": "Dataset"},
        pluck="measure",
        order_by="idx asc",
    )
    rerouted = fact[0].reroute_measure
    if rerouted:
        names = [n for n in names if n != rerouted]
    return names or list(_DEFAULT_BASE_MEASURE_NAMES)


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

    # erp_sources = the enabled connectors' types, else the d365_fo default.
    # NOT the existing file value: re-reading it would make deleting the last
    # connector a no-op (its erp_type would persist from the stale file), so the
    # registry could never be fully drained. The registry is authoritative; with
    # no enabled connectors we fall back to the seeded default, not the old file.
    erp_sources = _build_erp_sources_vars() or ["d365_fo"]
    new_vars["erp_sources"] = erp_sources

    # Merge and write
    updated = _merge_vars_into_yaml(original, new_vars)

    with open(path, "w") as f:
        yaml.dump(updated, f, default_flow_style=False, sort_keys=False,
                  allow_unicode=True)


# ---------------------------------------------------------------------------
# Gold model -> Build Governance domain tags
# ---------------------------------------------------------------------------

def _apply_model_domains(project, mapping):
    """Set each gold model's domain tag from mapping {model_name: domain}.

    Pure (no Frappe): mutates and returns the parsed dbt_project.yml dict.
    Rewrites only `models.open_epm.gold.<model>.+tags` to
    ['gold', 'domain:<domain>']; the gold layer's own config (+schema,
    +materialized, +tags) and any models not in the mapping are left untouched.
    """
    gold = (((project or {}).get("models") or {}).get("open_epm") or {}).get("gold")
    if not isinstance(gold, dict):
        return project
    for model_name, domain in mapping.items():
        cfg = gold.get(model_name)
        if not isinstance(cfg, dict):
            cfg = {}
        cfg["+tags"] = ["gold", f"domain:{domain}"]
        gold[model_name] = cfg
    return project


def _build_model_domain_mapping():
    """Build {model_name: build_domain} from the Build Model doctype."""
    docs = frappe.get_all(
        "Build Model",
        fields=["model_name", "build_domain"],
        order_by="model_name asc",
        limit_page_length=0,
    )
    return {d.model_name: d.build_domain for d in docs if d.build_domain}


def regenerate_model_domains():
    """Write the gold models' domain tags into dbt_project.yml from Build Model docs.

    Frappe is the source of truth for the model -> domain assignment that drives
    Build Governance scope selection. When no Build Model docs exist the YAML is
    left untouched (nothing to manage yet).
    """
    path = _get_dbt_project_path()

    try:
        with open(path) as f:
            project = yaml.safe_load(f)
    except FileNotFoundError:
        frappe.logger().warning(
            f"dbt_project.yml not found at {path} — skipping model-domain "
            f"regeneration. Set dbt_project_path in EPM Settings if dbt is on a "
            f"different host."
        )
        return

    mapping = _build_model_domain_mapping()
    if not mapping:
        return

    project = _apply_model_domains(project, mapping)

    with open(path, "w") as f:
        yaml.dump(project, f, default_flow_style=False, sort_keys=False,
                  allow_unicode=True)

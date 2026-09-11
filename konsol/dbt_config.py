"""dbt_project.yml vars regenerator for EPM config doctypes.

Reads Dimension, Measure, and Fiscal Period docs from Frappe and
regenerates the vars section of dbt_project.yml, preserving all
non-vars sections (models, seeds, paths, etc.).
"""
import re

import frappe
import yaml



def _get_dbt_project_base():
    """Get the dbt project base dir from EPM Settings."""
    settings = frappe.get_single("EPM Settings")
    return settings.dbt_project_path or "/home/pd/open_epm/dbt_project"


def _get_dbt_project_path():
    """Get dbt_project.yml path from EPM Settings."""
    return f"{_get_dbt_project_base()}/dbt_project.yml"


# ---------------------------------------------------------------------------
# Surgical vars ownership (F3)
# ---------------------------------------------------------------------------
# konsol owns EXACTLY these vars keys, and writes them ONLY between the marker
# comments below. Everything outside the markers — comments, formatting,
# erp_sources, cluster_enabled, every hand-written line — is preserved byte
# for byte.
#
# The previous implementation round-tripped the whole file through
# yaml.safe_load/yaml.dump: every comment stripped (28 -> 0, three times in
# one day via bench migrate), keys it never owned deleted (cluster_enabled)
# or overwritten (erp_sources, from connector state — which enabled models
# whose raw data had never landed and broke the entire build, #139).
#
# erp_sources is deliberately NOT managed: a connector being enabled says
# nothing about its data having ever landed, so which ERPs the build trusts
# stays a deliberate, committed engineering decision.

MANAGED_KEYS = ("dimensions", "base_measures", "fiscal_extra_periods",
                "fiscal_quarter_mapping", "fiscal_half_mapping")
MANAGED_BEGIN = "  # --- BEGIN konsol-managed vars (regenerated; do not edit by hand) ---"
MANAGED_END = "  # --- END konsol-managed vars ---"


def render_managed_vars(managed):
    """Render the managed keys as a vars-indented YAML block. Pure.

    An empty mapping renders as markers and nothing else. yaml.dump({}) is the
    flow scalar "{}", which indented under ``vars:`` produces

        vars:
          erp_sources: [d365_fo]
          {}

    — a mapping value where a key is expected, so the whole dbt_project.yml
    stops parsing (yaml.scanner.ScannerError). A site with no Published
    Dimension, Measure or Fiscal Period hits that on its first regenerate, and
    nothing downstream can read the file again until a human edits it.
    """
    if not managed:
        return MANAGED_BEGIN + "\n" + MANAGED_END
    text = yaml.dump(managed, default_flow_style=False, sort_keys=False,
                     allow_unicode=True)
    indented = "".join(
        ("  " + line if line.strip() else line) + "\n"
        for line in text.rstrip("\n").split("\n")
    )
    return MANAGED_BEGIN + "\n" + indented + MANAGED_END


MANAGED_DOMAINS_BEGIN = "      # --- BEGIN konsol-managed model domains (regenerated; do not edit by hand) ---"
MANAGED_DOMAINS_END = "      # --- END konsol-managed model domains ---"


def splice_managed_block(text, rendered_block, begin=MANAGED_BEGIN, end=MANAGED_END):
    """Replace the marker-delimited region with rendered_block. Pure.

    Returns the new text, or None when the markers are absent — the caller
    must then refuse to write. Never fabricates a region: a file without
    markers is a file konsol does not own any part of.
    """
    b = text.find(begin)
    e = text.find(end)
    if b == -1 or e == -1 or e < b:
        return None
    e += len(end)
    return text[:b] + rendered_block + text[e:]


# A dbt model name is a file stem and a build_domain is a tag fragment; both are
# interpolated straight into YAML here, so both are restricted to characters
# that cannot end a scalar, open a comment or start a new key. Without this a
# Build Model named `x: y` or `x #c` — an ordinary Frappe Data field, editable
# by anyone who can edit Build Model — rewrites dbt_project.yml into something
# that either fails to parse or silently re-tags other models on the next save.
_SAFE_MODEL_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_SAFE_DOMAIN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")


class UnsafeDomainMapping(ValueError):
    """A Build Model row cannot be rendered into YAML safely."""


def render_model_domains(mapping):
    """Render the gold per-model domain entries, grouped by domain. Pure.

    mapping is {model_name: domain}; each entry becomes
        <model>:\n  +tags: ['gold', 'domain:<domain>']
    at the gold subtree's 6-space indent, with a generated header per domain
    group. The whole region is machine-owned.

    Raises UnsafeDomainMapping when a name would not survive interpolation —
    the caller refuses to write the file rather than corrupting it.
    """
    for model, domain in mapping.items():
        if not _SAFE_MODEL_NAME.match(str(model)):
            raise UnsafeDomainMapping(
                f"Build Model name {model!r} is not a valid dbt model name "
                f"(letters, digits and underscore only) — dbt_project.yml NOT written."
            )
        if not _SAFE_DOMAIN.match(str(domain)):
            raise UnsafeDomainMapping(
                f"build_domain {domain!r} on model {model!r} is not a valid tag "
                f"(letters, digits, '_', '-', '.') — dbt_project.yml NOT written."
            )

    lines = [MANAGED_DOMAINS_BEGIN]
    by_domain = {}
    for model, domain in mapping.items():
        by_domain.setdefault(domain, []).append(model)
    for domain in sorted(by_domain):
        lines.append(f"      # --- Domain: {domain} ---")
        for model in sorted(by_domain[domain]):
            lines.append(f"      {model}:")
            lines.append(f"        +tags: ['gold', 'domain:{domain}']")
    lines.append(MANAGED_DOMAINS_END)
    return "\n".join(lines)








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



def regenerate_vars():
    """Write the konsol-managed vars into dbt_project.yml, surgically.

    Only the marker-delimited region changes; the file is otherwise preserved
    byte for byte. Missing markers mean this konsolidat checkout predates the
    managed region (or someone removed it) — log and do nothing, never
    rewrite: silently reformatting an engineering config file is how three
    migrates in one day destroyed its documentation.
    """
    path = _get_dbt_project_path()
    try:
        with open(path) as f:
            text = f.read()
    except FileNotFoundError:
        frappe.logger().warning(
            f"dbt_project.yml not found at {path} — skipping vars regeneration. "
            f"Set dbt_project_path in EPM Settings if dbt is on a different host."
        )
        return

    managed = {}
    dimensions = _build_dimensions_vars()
    if dimensions:
        managed["dimensions"] = dimensions
    measures = _build_measures_vars()
    if measures:
        managed["base_measures"] = measures
    managed.update(_build_fiscal_vars())

    updated = splice_managed_block(text, render_managed_vars(managed))
    if updated is None:
        frappe.logger().warning(
            "dbt_project.yml has no konsol-managed markers — vars NOT written. "
            "Add the BEGIN/END konsol-managed comments around the "
            "dimensions/base_measures/fiscal vars to opt in."
        )
        return
    if updated != text:
        with open(path, "w") as f:
            f.write(updated)


# ---------------------------------------------------------------------------
# Gold model -> Build Governance domain tags
# ---------------------------------------------------------------------------


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
    """Write the gold models' domain tags into dbt_project.yml, surgically.

    Frappe (Build Model) is the source of truth for model -> domain, but only
    the marker-delimited domains region changes — the previous implementation
    yaml.dump'd the ENTIRE file, and deleting orphaned Build Model docs during
    a bench migrate fired on_trash and reformatted dbt_project.yml wholesale.
    Missing markers -> log and refuse, same contract as regenerate_vars().
    """
    path = _get_dbt_project_path()
    try:
        with open(path) as f:
            text = f.read()
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

    try:
        rendered = render_model_domains(mapping)
    except UnsafeDomainMapping as e:
        # Refuse the whole write rather than tag some models and not others —
        # same contract as a missing marker region.
        frappe.logger().warning(f"model domains NOT written: {e}")
        return

    updated = splice_managed_block(
        text, rendered,
        begin=MANAGED_DOMAINS_BEGIN, end=MANAGED_DOMAINS_END,
    )
    if updated is None:
        frappe.logger().warning(
            "dbt_project.yml has no konsol-managed model-domain markers — "
            "domains NOT written. Add the BEGIN/END markers around the gold "
            "per-model entries to opt in."
        )
        return
    if updated != text:
        with open(path, "w") as f:
            f.write(updated)

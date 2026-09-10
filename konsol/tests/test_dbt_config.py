"""TDD tests for konsol.dbt_config — dbt_project.yml vars regenerator."""
import ast
import os
import tempfile
import shutil

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DBT_CONFIG_PATH = os.path.join(APP_DIR, "dbt_config.py")
DBT_PROJECT_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(APP_DIR)))),
    "open_epm", "dbt_project", "dbt_project.yml"
)


def test_dbt_config_module_exists():
    """dbt_config.py must exist."""
    assert os.path.exists(DBT_CONFIG_PATH)


def test_dbt_config_has_regenerate_vars():
    """Must expose regenerate_vars() function."""
    with open(DBT_CONFIG_PATH) as f:
        tree = ast.parse(f.read())
    func_names = [n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]
    assert "regenerate_vars" in func_names


def test_dbt_config_reads_yaml():
    """Must import yaml for YAML handling."""
    with open(DBT_CONFIG_PATH) as f:
        content = f.read()
    assert "yaml" in content.lower()


def test_dbt_config_preserves_non_vars():
    """F3: preservation is structural now — the writer splices ONLY the
    marker-delimited region, so everything else survives byte for byte (the
    old _merge_vars_into_yaml round-tripped the whole file and stripped every
    comment)."""
    import sys
    sys.path.insert(0, APP_DIR)
    from dbt_config import render_managed_vars, splice_managed_block

    doc = (
        "name: open_epm\n"
        "# hand comment\n"
        "vars:\n"
        "  erp_sources:\n"
        "  - d365_fo\n"
        + render_managed_vars({"dimensions": [{"name": "old_dim"}]})
        + "\nmodels:\n  open_epm:\n    gold:\n      +schema: gold\n"
    )
    out = splice_managed_block(
        doc, render_managed_vars({"dimensions": [{"name": "new_dim"}]}))
    assert "new_dim" in out and "old_dim" not in out
    assert "# hand comment" in out
    assert "+schema: gold" in out
    assert "erp_sources" in out

def test_dbt_config_round_trip():
    """Read real dbt_project.yml, merge vars, verify structure preserved."""
    if not os.path.exists(DBT_PROJECT_PATH):
        return  # Skip if dbt_project not available

    import yaml
    with open(DBT_PROJECT_PATH) as f:
        original = yaml.safe_load(f)

    import sys
    sys.path.insert(0, APP_DIR)
    from dbt_config import _merge_vars_into_yaml

    # Re-merge with same vars — should be idempotent
    result = _merge_vars_into_yaml(original, original.get("vars", {}))

    assert result["name"] == original["name"]
    assert result.get("models") == original.get("models")
    assert result.get("seeds") == original.get("seeds")


# --- Gold model -> domain tags ---

def test_dbt_config_has_regenerate_model_domains():
    """Must expose regenerate_model_domains() and the pure _apply_model_domains()."""
    with open(DBT_CONFIG_PATH) as f:
        tree = ast.parse(f.read())
    func_names = [n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]
    assert "regenerate_model_domains" in func_names
    # F3: the whole-file rewriter (_apply_model_domains + yaml.dump) is gone;
    # domains render into their own marker region.
    assert "_apply_model_domains" not in func_names
    assert "render_model_domains" in func_names


def test_regenerate_model_domains_reads_build_model_doctype():
    """regenerate_model_domains must source the mapping from the Build Model doctype."""
    with open(DBT_CONFIG_PATH) as f:
        content = f.read()
    assert '"Build Model"' in content


def test_render_model_domains_tags_and_refusal():
    """F3 replacement for the _apply_model_domains tests: rendering produces
    the same +tags contract, and a file without markers is REFUSED rather than
    rewritten."""
    import sys
    sys.path.insert(0, APP_DIR)
    from dbt_config import (render_model_domains, splice_managed_block,
                            MANAGED_DOMAINS_BEGIN, MANAGED_DOMAINS_END)

    out = render_model_domains({"gold_x": "consolidation"})
    assert "+tags: ['gold', 'domain:consolidation']" in out
    assert splice_managed_block(
        "models: {}", out,
        begin=MANAGED_DOMAINS_BEGIN, end=MANAGED_DOMAINS_END) is None



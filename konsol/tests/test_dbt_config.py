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
    """regenerate_vars must preserve non-vars sections (models, seeds, etc.)."""
    import sys
    sys.path.insert(0, APP_DIR)
    from dbt_config import _merge_vars_into_yaml

    original = {
        "name": "open_epm",
        "version": "1.0.0",
        "models": {"open_epm": {"gold": {"+schema": "gold"}}},
        "vars": {"dimensions": [{"name": "old_dim"}]},
    }
    new_vars = {"dimensions": [{"name": "new_dim"}]}
    result = _merge_vars_into_yaml(original, new_vars)

    assert result["name"] == "open_epm"
    assert result["version"] == "1.0.0"
    assert result["models"] == {"open_epm": {"gold": {"+schema": "gold"}}}
    assert result["vars"]["dimensions"] == [{"name": "new_dim"}]


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


# --- ERP Source management ---

def test_dbt_config_has_build_erp_sources():
    """Must expose a builder that reads erp_sources from the ERP Source doctype."""
    with open(DBT_CONFIG_PATH) as f:
        tree = ast.parse(f.read())
    func_names = [n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]
    assert "_build_erp_sources_vars" in func_names


def test_regenerate_reads_erp_source_doctype():
    """regenerate_vars must source erp_sources from the ERP Source doctype."""
    with open(DBT_CONFIG_PATH) as f:
        content = f.read()
    assert '"ERP Source"' in content
    # The generated key written into vars is `erp_sources`.
    assert '"erp_sources"' in content


def test_regenerate_preserves_existing_vars():
    """regenerate_vars must seed from existing vars, not start from {}.

    Otherwise any manual/unmanaged var (e.g. erp_sources before it was managed)
    is clobbered on every run — the original bug.
    """
    with open(DBT_CONFIG_PATH) as f:
        body = f.read().split("def regenerate_vars")[1]
    # Seeds new_vars from the existing vars block...
    assert 'get("vars")' in body
    # ...and does NOT reset it to an empty dict.
    assert "new_vars = {}" not in body

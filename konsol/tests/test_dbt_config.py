"""TDD tests for konsol.dbt_config — dbt_project.yml vars regenerator."""
import ast
import importlib.util
import os
import sys
import types

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DBT_CONFIG_PATH = os.path.join(APP_DIR, "dbt_config.py")
DBT_PROJECT_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(APP_DIR)))),
    "open_epm", "dbt_project", "dbt_project.yml"
)


def _load():
    """Import dbt_config with frappe stubbed, the test_budget_grain pattern.

    `sys.path.insert(APP_DIR); from dbt_config import ...` was used here, and
    dbt_config imports frappe at module level — so on any host without Frappe
    (the CI runner, this bench) every test that did it raised
    ModuleNotFoundError and the runner counted it as a missing dependency, not
    a failure. That is how test_dbt_config_round_trip went on "passing" for
    months while importing a function that no longer exists.
    """
    if "frappe" not in sys.modules:
        sys.modules["frappe"] = types.ModuleType("frappe")
    spec = importlib.util.spec_from_file_location(
        "dbt_config_under_test", DBT_CONFIG_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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
    m = _load()
    render_managed_vars, splice_managed_block = (
        m.render_managed_vars, m.splice_managed_block)

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
    """Re-splice the real dbt_project.yml's own managed vars: the file must come
    back parseable, with everything outside the markers byte-identical.

    Replaces the _merge_vars_into_yaml round-trip test — F3 deleted that
    function, so this test could only ever ImportError (where a dbt_project
    exists) or return early (everywhere else).
    """
    if not os.path.exists(DBT_PROJECT_PATH):
        return  # dbt_project lives on the Frappe host only

    import yaml
    m = _load()

    with open(DBT_PROJECT_PATH) as f:
        text = f.read()
    if m.MANAGED_BEGIN not in text:
        return  # a checkout predating the managed region

    original = yaml.safe_load(text)
    managed = {k: original["vars"][k]
               for k in m.MANAGED_KEYS if k in original.get("vars", {})}

    out = m.splice_managed_block(text, m.render_managed_vars(managed))
    assert out is not None

    result = yaml.safe_load(out)
    assert result["name"] == original["name"]
    assert result.get("models") == original.get("models")
    # Hand-owned vars survive — the whole point of the surgical writer.
    assert result["vars"].get("erp_sources") == original["vars"].get("erp_sources")
    assert result["vars"].get("cluster_enabled") == original["vars"].get("cluster_enabled")
    # And literally nothing outside the markers moved.
    assert out.split(m.MANAGED_BEGIN)[0] == text.split(m.MANAGED_BEGIN)[0]
    assert out.split(m.MANAGED_END)[-1] == text.split(m.MANAGED_END)[-1]


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
    m = _load()

    out = m.render_model_domains({"gold_x": "consolidation"})
    assert "+tags: ['gold', 'domain:consolidation']" in out
    assert m.splice_managed_block(
        "models: {}", out,
        begin=m.MANAGED_DOMAINS_BEGIN, end=m.MANAGED_DOMAINS_END) is None


def test_regenerate_model_domains_refuses_unsafe_names():
    """A Build Model name that would break out of its YAML scalar must stop the
    write, not corrupt dbt_project.yml."""
    src = open(DBT_CONFIG_PATH).read()
    body = src.split("def regenerate_model_domains")[1]
    assert "except UnsafeDomainMapping" in body
    assert "return" in body.split("except UnsafeDomainMapping")[1]



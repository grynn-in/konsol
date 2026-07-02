"""Structural tests for Reporting Hierarchy doctypes + seed regeneration."""
import json
import os

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

SEED_COLUMNS = [
    "hierarchy_name", "dimension", "member_code", "member_label",
    "parent_member_code", "is_group", "hierarchy_level", "path",
    "effective_from", "effective_to", "is_default", "status",
]


def _doctype_json(name):
    with open(os.path.join(APP_DIR, "epm", "doctype", name, f"{name}.json")) as f:
        return json.load(f)


def _read(rel):
    with open(os.path.join(APP_DIR, rel)) as f:
        return f.read()


def test_reporting_hierarchy_doctype():
    meta = _doctype_json("reporting_hierarchy")
    assert meta["module"] == "EPM"
    assert meta["autoname"] == "field:hierarchy_name"
    fields = {f["fieldname"] for f in meta["fields"]}
    for f in ["hierarchy_name", "dimension", "label", "status", "is_default"]:
        assert f in fields


def test_reporting_hierarchy_member_doctype():
    meta = _doctype_json("reporting_hierarchy_member")
    assert meta["module"] == "EPM"
    fields = {f["fieldname"] for f in meta["fields"]}
    for f in ["reporting_hierarchy", "parent_member", "member_code", "is_group"]:
        assert f in fields


def test_header_publish_regenerates_seed_and_reporting_rebuild():
    src = _read(os.path.join("epm", "doctype", "reporting_hierarchy", "reporting_hierarchy.py"))
    assert "regenerate_reporting_hierarchies_seed" in src
    assert 'scope=_REPORTING_BUILD_SCOPE' in src or 'scope="reporting"' in src


def test_dbt_config_seed_columns():
    src = _read("dbt_config.py")
    assert "def regenerate_reporting_hierarchies_seed" in src
    assert "reporting_hierarchies.csv" in src
    for col in SEED_COLUMNS:
        assert col in src


def test_flatten_module_exists():
    src = _read("reporting_hierarchy_seed.py")
    assert "def flatten_reporting_hierarchies" in src


def test_tasks_reporting_scope():
    src = _read("tasks.py")
    assert '"reporting": "+tag:domain:reporting"' in src
    assert '"reporting"' in src or "'reporting'" in src


def test_api_get_reporting_hierarchy_tree():
    src = _read("api.py")
    assert "def get_reporting_hierarchy_tree" in src


def test_build_models_include_hierarchy_v2():
    models = json.load(open(os.path.join(APP_DIR, "fixtures", "build_model.json")))
    names = {m["model_name"] for m in models}
    for model in (
        "gold_tb_at_hierarchy_node",
        "gold_budget_at_hierarchy_node",
        "gold_variance_at_hierarchy_node",
    ):
        assert model in names


def test_fixtures_registered():
    hooks = _read("hooks.py")
    assert '"Reporting Hierarchy"' in hooks
    assert '"Reporting Hierarchy Member"' in hooks


def test_reporting_hierarchy_seed_unit():
    from konsol.reporting_hierarchy_seed import _ancestor_chain

    members = {
        "root": type("M", (), {"parent_member": None})(),
        "child": type("M", (), {"parent_member": "root"})(),
    }
    chain = _ancestor_chain(members["child"], members)
    assert chain == ["root"]
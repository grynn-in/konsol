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


def test_header_publish_resyncs_staging_and_reporting_rebuild():
    src = _read(os.path.join("epm", "doctype", "reporting_hierarchy", "reporting_hierarchy.py"))
    # F3: publish re-syncs epm_staging via the computed resync_staging()
    # pattern (rows are flattened, not field-mapped); no CSV seed is written.
    # The publish/unpublish lifecycle itself is the shared one; this doctype
    # supplies the computed _resync() and its own build scope.
    assert "class ReportingHierarchy(GovernedReferenceDocument)" in src
    assert "def resync_staging" in src
    assert "def _resync" in src
    assert 'CH_STAGING_TABLE = "epm_staging.reporting_hierarchies"' in src
    assert "flatten_reporting_hierarchies" in src
    assert "regenerate_reporting_hierarchies_seed" not in src
    assert "BUILD_SCOPE = _REPORTING_BUILD_SCOPE" in src
    assert '_REPORTING_BUILD_SCOPE = "reporting"' in src


def test_resync_staging_returns_what_was_written():
    """It used to return len(data) unconditionally, so a failed ClickHouse
    write still reported a row count and reconcile logged a clean repair with
    no watermark behind it. Return sync_table's own result."""
    src = _read(os.path.join("epm", "doctype", "reporting_hierarchy", "reporting_hierarchy.py"))
    body = src.split("def resync_staging")[1].split("\n    def ")[0]
    assert "return sync_table(" in body
    assert "return len(data)" not in body


def test_staging_columns_match_the_flattened_contract():
    """F3: the columns dbt reads now travel via CH_STAGING_COLUMNS."""
    src = _read(os.path.join("epm", "doctype", "reporting_hierarchy", "reporting_hierarchy.py"))
    for col in SEED_COLUMNS:
        assert f'"{col}"' in src, f"column {col} missing from CH_STAGING_COLUMNS"


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


def test_reporting_hierarchy_is_not_a_fixture():
    """MGMT_DEMO was the Contoso demo's management tree. Hierarchies are built
    per site, and a fixture would force-reimport the demo's over them."""
    src = _read("hooks.py")
    fixtures = src.split("fixtures = [")[1].split("]")[0]
    entries = [l.strip() for l in fixtures.splitlines() if l.strip().startswith('"')]
    for doctype in ("Reporting Hierarchy", "Reporting Hierarchy Member"):
        assert f'"{doctype}",' not in entries, doctype


def test_reporting_hierarchy_seed_unit():
    from konsol.reporting_hierarchy_seed import _ancestor_chain

    members = {
        "root": type("M", (), {"parent_member": None})(),
        "child": type("M", (), {"parent_member": "root"})(),
    }
    chain = _ancestor_chain(members["child"], members)
    assert chain == ["root"]
"""TDD tests for Allocation Rule and Allocation Driver doctypes."""
import ast
import glob
import json
import os

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXTURES_DIR = os.path.join(APP_DIR, "fixtures")


def _doctype_file(doctype_dir, ext):
    """Locate a doctype file in whichever konsol module owns it.

    Allocation/consolidation doctypes live under their own modules
    (konsol/allocation, konsol/consolidation), not konsol/epm — resolve
    dynamically so the tests survive module reorganisation.
    """
    matches = glob.glob(os.path.join(
        APP_DIR, "*", "doctype", doctype_dir, f"{doctype_dir}.{ext}"))
    return matches[0] if matches else None


def _load_json(doctype_dir):
    with open(_doctype_file(doctype_dir, "json")) as f:
        return json.load(f)


def _load_py(doctype_dir):
    with open(_doctype_file(doctype_dir, "py")) as f:
        return f.read()


# --- Allocation Rule ---

def test_allocation_rule_json_exists():
    assert _doctype_file("allocation_rule", "json") is not None


def test_allocation_rule_has_required_fields():
    meta = _load_json("allocation_rule")
    fields = [f["fieldname"] for f in meta["fields"]]
    for f in ["allocation_rule_id", "rule_name", "step_order", "source_account",
              "source_cost_center", "driver_type", "target_account"]:
        assert f in fields, f"Missing field: {f}"


def test_allocation_rule_id_unique():
    meta = _load_json("allocation_rule")
    for field in meta["fields"]:
        if field["fieldname"] == "allocation_rule_id":
            assert field.get("unique") == 1


def test_allocation_rule_ch_sync():
    content = _load_py("allocation_rule")
    assert "sync_doctype" in content
    assert "gold.allocation_rules" in content


def test_allocation_rule_driver_type_options():
    meta = _load_json("allocation_rule")
    for field in meta["fields"]:
        if field["fieldname"] == "driver_type":
            options = field["options"].split("\n")
            assert "headcount" in options
            assert "revenue" in options
            assert "sqm" in options


def test_allocation_rule_fixture_matches_d365_demo():
    """Demo D365 entities use SALES/HQ/PROD cost centers — not USMF 7100/IT."""
    with open(os.path.join(FIXTURES_DIR, "allocation_rule.json")) as handle:
        rules = json.load(handle)
    assert len(rules) == 3
    source_ccs = {rule["source_cost_center"] for rule in rules}
    assert source_ccs == {"SALES", "HQ", "PROD"}
    accounts = {rule["source_account"] for rule in rules}
    assert accounts == {"6010"}


def test_allocation_driver_fixture_exists_and_covers_demo_entities():
    path = os.path.join(FIXTURES_DIR, "allocation_driver.json")
    assert os.path.isfile(path)
    with open(path) as handle:
        drivers = json.load(handle)
    assert len(drivers) == 108
    entities = {row["data_area_id"] for row in drivers}
    assert entities == {"AMUS", "AMHQ", "AMDE"}


def test_after_migrate_syncs_allocation_config():
    with open(os.path.join(APP_DIR, "install.py")) as handle:
        src = handle.read()
    assert "_sync_allocation_config_to_clickhouse" in src
    after = src.split("def after_migrate")[1].split("\ndef ")[0]
    assert "_sync_allocation_config_to_clickhouse()" in after
    assert "from konsol.allocation.bootstrap import sync_allocation_config_to_clickhouse" in src


def test_allocation_run_syncs_after_commit():
    content = _load_py("allocation_run")
    assert "after_commit" in content
    assert "sync_allocation_runs_to_clickhouse" in content
    assert 'filters={"docstatus": ["in", [1, 2]]}' in content


def test_allocation_bootstrap_syncs_runs():
    with open(os.path.join(APP_DIR, "allocation", "bootstrap.py")) as handle:
        content = handle.read()
    assert "_sync_allocation_runs" in content
    after = content.split("def sync_allocation_config_to_clickhouse")[1].split("\ndef ")[0]
    assert "_sync_allocation_runs()" in after


def test_allocation_bootstrap_exports_sync_helper():
    path = os.path.join(APP_DIR, "allocation", "bootstrap.py")
    assert os.path.isfile(path)
    with open(path) as handle:
        content = handle.read()
    assert "def sync_allocation_config_to_clickhouse" in content
    assert "epm_staging.allocation_rules" in content
    assert "epm_staging.allocation_drivers" in content


# --- Allocation Run (PRD-10: submit requests a scoped governed build) ---

def _load_workflow_json():
    path = os.path.join(
        APP_DIR, "allocation", "doctype", "allocation_run",
        "allocation_run_workflow.json")
    with open(path) as handle:
        return json.load(handle)


def _module_constant(content, name):
    """Extract a module-level string constant via AST."""
    tree = ast.parse(content)
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if getattr(target, "id", None) == name:
                    return node.value.value
    return None


def test_allocation_run_submit_requests_governed_build():
    """Submitting a run must request a governed build and link the PBR."""
    content = _load_py("allocation_run")
    assert "request_governed_rebuild" in content
    tree = ast.parse(content)
    methods = [n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]
    assert "before_submit" in methods
    assert "self.build_approval = request_governed_rebuild" in content


def test_allocation_run_build_scope_matches_build_model_fixture():
    """The scope must be the build_domain of the gold_allocation_* models."""
    content = _load_py("allocation_run")
    scope = _module_constant(content, "_ALLOCATION_BUILD_SCOPE")
    assert scope == "consolidation"
    with open(os.path.join(FIXTURES_DIR, "build_model.json")) as handle:
        models = json.load(handle)
    alloc_domains = {
        model["build_domain"] for model in models
        if model["model_name"].startswith("gold_allocation_")
    }
    assert alloc_domains == {scope}


def test_allocation_run_has_build_approval_link_field():
    meta = _load_json("allocation_run")
    field = next(
        (f for f in meta["fields"] if f["fieldname"] == "build_approval"), None)
    assert field is not None
    assert field["fieldtype"] == "Link"
    assert field["options"] == "Build Approval"
    assert field.get("read_only") == 1


def test_allocation_run_ch_surface_unchanged():
    """build_approval must NOT enter the ClickHouse field map (PRD-10 §4)."""
    content = _load_py("allocation_run")
    assert 'RUN_CH_TABLE = "epm_staging.allocation_runs"' in content
    tree = ast.parse(content)
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(
            getattr(t, "id", None) == "RUN_CH_FIELD_MAP" for t in node.targets
        ):
            keys = [k.value for k in node.value.keys]
            assert keys == [
                "allocation_run_id", "fiscal_year", "fiscal_period",
                "status", "run_by", "run_at", "reversal_of",
            ]
            return
    raise AssertionError("RUN_CH_FIELD_MAP not found")


def test_allocation_run_workflow_has_no_unreachable_state():
    """No dead 'Running' state; every non-initial state is reachable."""
    workflow = _load_workflow_json()
    states = {s["state"] for s in workflow["states"]}
    assert "Running" not in states
    reachable = {"Draft"} | {t["next_state"] for t in workflow["transitions"]}
    assert states == reachable
    for transition in workflow["transitions"]:
        assert transition["state"] in states
        assert transition["next_state"] in states


def test_allocation_run_status_options_have_no_dead_state():
    meta = _load_json("allocation_run")
    status = next(f for f in meta["fields"] if f["fieldname"] == "status")
    assert status["options"].split("\n") == ["Draft", "Active", "Reversed"]


# --- Allocation Driver ---

def test_allocation_driver_json_exists():
    assert _doctype_file("allocation_driver", "json") is not None


def test_allocation_driver_has_required_fields():
    meta = _load_json("allocation_driver")
    fields = [f["fieldname"] for f in meta["fields"]]
    for f in ["driver_type", "data_area_id", "cost_center", "driver_value",
              "fiscal_year", "fiscal_period"]:
        assert f in fields, f"Missing field: {f}"


def test_allocation_driver_splits_by_type():
    """Must sync to separate CH tables per driver_type."""
    content = _load_py("allocation_driver")
    assert "allocation_drivers_headcount" in content or "allocation_drivers_{" in content
    # Must filter by driver_type
    assert "driver_type" in content


def test_allocation_driver_ch_sync_all_types():
    """Must handle headcount, revenue, sqm types."""
    content = _load_py("allocation_driver")
    for dtype in ["headcount", "revenue", "sqm"]:
        assert dtype in content


def test_allocation_driver_has_on_update():
    content = _load_py("allocation_driver")
    tree = ast.parse(content)
    methods = [n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]
    assert "on_update" in methods
    assert "on_trash" in methods

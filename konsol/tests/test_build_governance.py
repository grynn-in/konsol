"""TDD tests for Pipeline Build Governance system.

Tests structural contracts via AST parsing — no Frappe runtime needed.
These tests define the API that tasks.py, api.py, install.py, and the
Pipeline Build Request doctype must satisfy.
"""
import ast
import json
import os

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TASKS_PATH = os.path.join(APP_DIR, "tasks.py")
API_PATH = os.path.join(APP_DIR, "api.py")
INSTALL_PATH = os.path.join(APP_DIR, "install.py")
HOOKS_PATH = os.path.join(APP_DIR, "hooks.py")
PBR_DIR = os.path.join(APP_DIR, "pipeline", "doctype", "pipeline_build_request")
PBR_JSON = os.path.join(PBR_DIR, "pipeline_build_request.json")
PBR_PY = os.path.join(PBR_DIR, "pipeline_build_request.py")
EPM_SETTINGS_JSON = os.path.join(
    APP_DIR, "pipeline", "doctype", "epm_settings", "epm_settings.json"
)

# The 9 doctypes that trigger builds on save
TRIGGER_DOCTYPES = [
    "Consolidation Group",
    "Consolidation Adjustment",
    "Ownership Period",
    "Historical Equity Rate",
    "IC Elimination Rule",
    "IC Balance",
    "Allocation Rule",
    "Allocation Driver",
    "Allocation Run",
]

VALID_SCOPES = {"staging", "actuals", "scenarios", "consolidation", "full"}
VALID_RISK_LEVELS = {"low", "medium", "high"}
VALID_WORKFLOW_STATES = {
    "Draft", "Pending Review", "Approved", "Running",
    "Completed", "Failed", "Cancelled",
}


def _parse(path):
    with open(path) as f:
        return ast.parse(f.read())


def _read(path):
    with open(path) as f:
        return f.read()


def _func_names(tree):
    return [n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]


# ===================================================================
# 1. DOCTYPE_BUILD_MAP in tasks.py
# ===================================================================

def test_doctype_build_map_exists():
    """tasks.py must define DOCTYPE_BUILD_MAP dict."""
    content = _read(TASKS_PATH)
    assert "DOCTYPE_BUILD_MAP" in content


def test_doctype_build_map_covers_all_trigger_doctypes():
    """DOCTYPE_BUILD_MAP must contain all 9 trigger doctypes."""
    tree = _parse(TASKS_PATH)
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "DOCTYPE_BUILD_MAP":
                    if isinstance(node.value, ast.Dict):
                        keys = []
                        for k in node.value.keys:
                            if isinstance(k, ast.Constant):
                                keys.append(k.value)
                        for dt in TRIGGER_DOCTYPES:
                            assert dt in keys, f"Missing doctype in map: {dt}"
                        return
    assert False, "DOCTYPE_BUILD_MAP assignment not found as a dict literal"


def test_doctype_build_map_values_have_scope():
    """Each entry in DOCTYPE_BUILD_MAP must specify a 'scope' key."""
    content = _read(TASKS_PATH)
    assert "'scope'" in content or '"scope"' in content


# ===================================================================
# 2. Preflight check in tasks.py
# ===================================================================

def test_preflight_check_exists():
    """tasks.py must define _preflight_check function."""
    funcs = _func_names(_parse(TASKS_PATH))
    assert "_preflight_check" in funcs


def test_preflight_check_references_raw_dependent_tags():
    """_preflight_check must reference raw-dependent scopes."""
    content = _read(TASKS_PATH)
    # Must check at least actuals scope
    assert "actuals" in content
    assert "staging" in content


# ===================================================================
# 3. run_governed_build in tasks.py
# ===================================================================

def test_run_governed_build_exists():
    """tasks.py must define run_governed_build function."""
    funcs = _func_names(_parse(TASKS_PATH))
    assert "run_governed_build" in funcs


def test_run_governed_build_uses_select_flag():
    """run_governed_build must use dbt --select for tag-based builds."""
    content = _read(TASKS_PATH)
    assert "--select" in content


def test_run_governed_build_references_domain_tags():
    """run_governed_build must reference domain: tag prefix."""
    content = _read(TASKS_PATH)
    assert "domain:" in content


# ===================================================================
# 4. on_consolidation_doc_update refactored
# ===================================================================

def test_on_consolidation_doc_update_exists():
    """tasks.py must still have on_consolidation_doc_update."""
    funcs = _func_names(_parse(TASKS_PATH))
    assert "on_consolidation_doc_update" in funcs


def test_on_consolidation_doc_update_uses_build_map():
    """on_consolidation_doc_update must reference DOCTYPE_BUILD_MAP."""
    tree = _parse(TASKS_PATH)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "on_consolidation_doc_update":
            body_src = ast.dump(node)
            assert "DOCTYPE_BUILD_MAP" in body_src
            return
    assert False, "on_consolidation_doc_update not found"


# ===================================================================
# 5. Pipeline Build Request doctype
# ===================================================================

def test_pbr_doctype_dir_exists():
    """pipeline_build_request directory must exist."""
    assert os.path.isdir(PBR_DIR)


def test_pbr_json_exists():
    """pipeline_build_request.json must exist."""
    assert os.path.isfile(PBR_JSON)


def test_pbr_json_valid():
    """pipeline_build_request.json must be valid JSON."""
    with open(PBR_JSON) as f:
        data = json.load(f)
    assert data["name"] == "Pipeline Build Request"


def test_pbr_has_autoname():
    """Pipeline Build Request must have autoname PBR-.#####."""
    with open(PBR_JSON) as f:
        data = json.load(f)
    assert data.get("autoname") == "PBR-.#####"


def test_pbr_has_required_fields():
    """Pipeline Build Request must have all governance fields."""
    with open(PBR_JSON) as f:
        data = json.load(f)
    field_names = {f["fieldname"] for f in data["fields"] if "fieldname" in f}

    required = {
        "build_scope", "risk_level", "trigger_source",
        "trigger_doctype", "trigger_docname",
        "workflow_state", "requested_by",
        "started_at", "completed_at", "duration_seconds",
        "build_output", "error_message", "preflight_result",
    }
    missing = required - field_names
    assert not missing, f"Missing fields: {missing}"


def test_pbr_build_scope_options():
    """build_scope must include all valid scopes."""
    with open(PBR_JSON) as f:
        data = json.load(f)
    for field in data["fields"]:
        if field.get("fieldname") == "build_scope":
            options = set(field["options"].split("\n"))
            assert VALID_SCOPES.issubset(options), f"Missing scopes: {VALID_SCOPES - options}"
            return
    assert False, "build_scope field not found"


def test_pbr_workflow_state_options():
    """workflow_state must include all valid states."""
    with open(PBR_JSON) as f:
        data = json.load(f)
    for field in data["fields"]:
        if field.get("fieldname") == "workflow_state":
            options = set(field["options"].split("\n"))
            assert VALID_WORKFLOW_STATES.issubset(options), (
                f"Missing states: {VALID_WORKFLOW_STATES - options}"
            )
            return
    assert False, "workflow_state field not found"


def test_pbr_has_sync_info_fields():
    """Pipeline Build Request must show Airbyte sync info."""
    with open(PBR_JSON) as f:
        data = json.load(f)
    field_names = {f["fieldname"] for f in data["fields"] if "fieldname" in f}
    sync_fields = {"sync_status_display", "sync_rows_display", "sync_time_display"}
    missing = sync_fields - field_names
    assert not missing, f"Missing sync info fields: {missing}"


def test_pbr_controller_exists():
    """pipeline_build_request.py controller must exist."""
    assert os.path.isfile(PBR_PY)


def test_pbr_controller_has_on_update():
    """Controller must implement on_update for workflow transitions."""
    funcs = _func_names(_parse(PBR_PY))
    assert "on_update" in funcs or "before_save" in funcs


# ===================================================================
# 6. EPM Settings — Airbyte sync status fields
# ===================================================================

def test_epm_settings_has_sync_status_fields():
    """EPM Settings must have Airbyte sync tracking fields."""
    with open(EPM_SETTINGS_JSON) as f:
        data = json.load(f)
    field_names = {f["fieldname"] for f in data["fields"] if "fieldname" in f}

    required = {"last_airbyte_sync_at", "last_airbyte_sync_status", "last_airbyte_sync_rows"}
    missing = required - field_names
    assert not missing, f"Missing EPM Settings fields: {missing}"


# ===================================================================
# 7. Airbyte webhook in api.py
# ===================================================================

def test_airbyte_webhook_exists():
    """api.py must define airbyte_sync_complete function."""
    funcs = _func_names(_parse(API_PATH))
    assert "airbyte_sync_complete" in funcs


def test_airbyte_webhook_updates_settings():
    """airbyte_sync_complete must reference EPM Settings."""
    content = _read(API_PATH)
    assert "EPM Settings" in content


# ===================================================================
# 8. Roles in install.py
# ===================================================================

def test_install_has_create_roles():
    """install.py must define _create_roles function."""
    funcs = _func_names(_parse(INSTALL_PATH))
    assert "_create_roles" in funcs


def test_install_creates_epm_roles():
    """_create_roles must create EPM User, EPM Analyst, EPM Admin."""
    content = _read(INSTALL_PATH)
    for role in ("EPM User", "EPM Analyst", "EPM Admin"):
        assert role in content, f"Missing role: {role}"


# ===================================================================
# 9. Hooks — after_migrate
# ===================================================================

def test_hooks_has_after_migrate():
    """hooks.py must register after_migrate hook."""
    content = _read(HOOKS_PATH)
    assert "after_migrate" in content

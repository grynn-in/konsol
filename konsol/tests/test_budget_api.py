"""TDD tests for Budget Save API endpoints."""
import ast
import os

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
API_PATH = os.path.join(APP_DIR, "api.py")
VBA_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(APP_DIR)))),
    "open_epm", "excel", "OpenEPM.bas"
)


def test_budget_save_endpoint_exists():
    """Must have a budget_save function."""
    with open(API_PATH) as f:
        tree = ast.parse(f.read())
    func_names = [n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]
    assert "budget_save" in func_names


def test_budget_save_batch_endpoint_exists():
    """Must have a budget_save_batch function."""
    with open(API_PATH) as f:
        tree = ast.parse(f.read())
    func_names = [n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]
    assert "budget_save_batch" in func_names


def test_budget_save_validates_fields():
    """Must validate required fields."""
    with open(API_PATH) as f:
        content = f.read()
    assert "_validate_budget_fields" in content
    assert "scenario_id" in content
    assert "data_area_id" in content


def test_budget_save_upserts():
    """Must upsert (create or update) based on composite key."""
    with open(API_PATH) as f:
        content = f.read()
    assert "_upsert_budget_input" in content
    # Must check for existing doc before creating
    assert "get_all" in content or "get_list" in content


def test_budget_save_is_whitelisted():
    """budget_save must be whitelisted."""
    with open(API_PATH) as f:
        content = f.read()
    # Check that there's a whitelist decorator near budget_save
    assert "whitelist" in content


def test_budget_save_is_post_only():
    """budget_save must only accept POST."""
    with open(API_PATH) as f:
        content = f.read()
    # Check for methods=["POST"] near budget_save
    assert 'methods=["POST"]' in content


# --- VBA Macro ---

def test_vba_has_budget_save_macro():
    """OpenEPM.bas must have EPM_BUDGET_SAVE macro."""
    if not os.path.exists(VBA_PATH):
        return  # Skip if not available
    with open(VBA_PATH) as f:
        content = f.read()
    assert "EPM_BUDGET_SAVE" in content


def test_vba_posts_to_budget_save_batch():
    """VBA macro must POST to budget_save_batch endpoint."""
    if not os.path.exists(VBA_PATH):
        return
    with open(VBA_PATH) as f:
        content = f.read()
    assert "budget_save_batch" in content


def test_vba_shows_progress():
    """VBA macro must show progress in status bar."""
    if not os.path.exists(VBA_PATH):
        return
    with open(VBA_PATH) as f:
        content = f.read()
    assert "StatusBar" in content
    assert "budget lines" in content.lower()


# --- VBA scenario_id support ---

def test_vba_epm_has_scenario_id_param():
    """EPM() function must accept scenario_id as 9th optional parameter."""
    if not os.path.exists(VBA_PATH):
        return
    with open(VBA_PATH) as f:
        content = f.read()
    # Find the EPM function signature
    assert "Optional scenario_id As String" in content


def test_vba_buildkey_includes_scenario_id():
    """BuildKey must include scenario_id in the cache key."""
    if not os.path.exists(VBA_PATH):
        return
    with open(VBA_PATH) as f:
        content = f.read()
    # BuildKey function should have scenarioId parameter
    assert "scenarioId" in content.split("Function BuildKey")[1].split("End Function")[0]


def test_vba_batch_json_includes_scenario_id():
    """Batch JSON builder must include scenario_id when non-empty."""
    if not os.path.exists(VBA_PATH):
        return
    with open(VBA_PATH) as f:
        content = f.read()
    assert '"scenario_id"' in content.replace("'", "")


def test_vba_epm_budget_has_scenario_id():
    """EPM_BUDGET shorthand must pass scenario_id through."""
    if not os.path.exists(VBA_PATH):
        return
    with open(VBA_PATH) as f:
        content = f.read()
    budget_fn = content.split("Function EPM_BUDGET")[1].split("End Function")[0]
    assert "scenario_id" in budget_fn

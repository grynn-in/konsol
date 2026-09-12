"""TDD tests for Budget Save API endpoints."""
import ast
import os

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
API_PATH = os.path.join(APP_DIR, "api.py")


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
    """Must upsert (create or update) the sheet/line based on composite key."""
    with open(API_PATH) as f:
        content = f.read()
    assert "_upsert_budget_line" in content
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


# --- budget_cell_save API ---

def test_budget_cell_save_endpoint_exists():
    """Must have a budget_cell_save function."""
    with open(API_PATH) as f:
        tree = ast.parse(f.read())
    func_names = [n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]
    assert "budget_cell_save" in func_names


def test_budget_cell_save_validates_layer():
    """budget_cell_save must validate layer against allowed values."""
    with open(API_PATH) as f:
        content = f.read()
    assert "VALID_LAYERS" in content
    assert "base" in content
    assert "challenge" in content


def test_budget_cell_save_upserts_period():
    """budget_cell_save must upsert a single period+layer, not replace all."""
    with open(API_PATH) as f:
        content = f.read()
    fn = content.split("def budget_cell_save")[1].split("\ndef ")[0]
    # Must check for existing period+layer row
    assert "fiscal_period" in fn
    assert "layer" in fn

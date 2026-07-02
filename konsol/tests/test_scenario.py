"""TDD tests for Scenario doctype."""
import ast
import json
import os

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DT_DIR = os.path.join(APP_DIR, "epm", "doctype", "scenario")
JSON_PATH = os.path.join(DT_DIR, "scenario.json")
PY_PATH = os.path.join(DT_DIR, "scenario.py")


def test_doctype_json_exists():
    """scenario.json must exist."""
    assert os.path.exists(JSON_PATH)


def test_doctype_py_exists():
    """scenario.py must exist."""
    assert os.path.exists(PY_PATH)


def test_doctype_module_is_epm():
    """Must belong to EPM module."""
    with open(JSON_PATH) as f:
        meta = json.load(f)
    assert meta["module"] == "EPM"


def test_doctype_has_required_fields():
    """Must have scenario_id, scenario_name, scenario_type, is_active fields."""
    with open(JSON_PATH) as f:
        meta = json.load(f)
    field_names = [f["fieldname"] for f in meta["fields"]]
    for required in ["scenario_id", "scenario_name", "scenario_type", "is_active"]:
        assert required in field_names, f"Missing field: {required}"


def test_scenario_id_is_unique():
    """scenario_id must be unique."""
    with open(JSON_PATH) as f:
        meta = json.load(f)
    for field in meta["fields"]:
        if field["fieldname"] == "scenario_id":
            assert field.get("unique") == 1
            break
    else:
        raise AssertionError("scenario_id field not found")


def test_autoname_by_scenario_id():
    """Must autoname by scenario_id field."""
    with open(JSON_PATH) as f:
        meta = json.load(f)
    assert meta["autoname"] == "field:scenario_id"


def test_scenario_type_options():
    """scenario_type must include actual, budget, forecast."""
    with open(JSON_PATH) as f:
        meta = json.load(f)
    for field in meta["fields"]:
        if field["fieldname"] == "scenario_type":
            options = field["options"].split("\n")
            assert "actual" in options
            assert "budget" in options
            assert "forecast" in options
            break


def test_py_has_on_update():
    """Must define on_update method for CH sync."""
    with open(PY_PATH) as f:
        tree = ast.parse(f.read())
    methods = []
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            methods.append(node.name)
    assert "on_update" in methods


def test_py_calls_sync_doctype():
    """on_update must call sync_doctype for CH sync."""
    with open(PY_PATH) as f:
        content = f.read()
    assert "sync_doctype" in content


def test_py_has_on_trash():
    """Must define on_trash for CH sync on delete."""
    with open(PY_PATH) as f:
        tree = ast.parse(f.read())
    methods = []
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            methods.append(node.name)
    assert "on_trash" in methods

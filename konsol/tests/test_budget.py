"""TDD tests for Budget Input, Budget Input Child, and Budget Workflow."""
import ast
import json
import os

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load_json(doctype_dir):
    path = os.path.join(APP_DIR, "epm", "doctype", doctype_dir, f"{doctype_dir}.json")
    with open(path) as f:
        return json.load(f)


def _load_py(doctype_dir):
    path = os.path.join(APP_DIR, "epm", "doctype", doctype_dir, f"{doctype_dir}.py")
    with open(path) as f:
        return f.read()


def _get_field_names(meta):
    return [f["fieldname"] for f in meta["fields"]]


# --- Budget Input Child (child table) ---

def test_budget_input_child_is_child_table():
    meta = _load_json("budget_input_child")
    assert meta["istable"] == 1


def test_budget_input_child_has_required_fields():
    meta = _load_json("budget_input_child")
    fields = _get_field_names(meta)
    for f in ["fiscal_period", "amount", "layer"]:
        assert f in fields, f"Missing field: {f}"


def test_budget_input_child_layer_options():
    meta = _load_json("budget_input_child")
    for field in meta["fields"]:
        if field["fieldname"] == "layer":
            options = field["options"].split("\n")
            assert "base" in options
            assert "challenge" in options
            assert "management" in options
            assert "board" in options


def test_budget_input_child_layer_default_is_base():
    meta = _load_json("budget_input_child")
    for field in meta["fields"]:
        if field["fieldname"] == "layer":
            assert field.get("default") == "base"


# --- Budget Input (parent) ---

def test_budget_input_json_exists():
    assert os.path.exists(os.path.join(
        APP_DIR, "epm", "doctype", "budget_input", "budget_input.json"))


def test_budget_input_has_required_fields():
    meta = _load_json("budget_input")
    fields = _get_field_names(meta)
    for f in ["scenario_id", "data_area_id", "fiscal_year", "main_account",
              "annual_amount", "periods"]:
        assert f in fields, f"Missing field: {f}"


def test_budget_input_scenario_is_link():
    meta = _load_json("budget_input")
    for field in meta["fields"]:
        if field["fieldname"] == "scenario_id":
            assert field["fieldtype"] == "Link"
            assert field["options"] == "Scenario Definition"


def test_budget_input_annual_amount_is_readonly():
    meta = _load_json("budget_input")
    for field in meta["fields"]:
        if field["fieldname"] == "annual_amount":
            assert field.get("read_only") == 1


def test_budget_input_periods_is_table():
    meta = _load_json("budget_input")
    for field in meta["fields"]:
        if field["fieldname"] == "periods":
            assert field["fieldtype"] == "Table"
            assert field["options"] == "Budget Input Child"


def test_budget_input_has_spread_profile_link():
    meta = _load_json("budget_input")
    for field in meta["fields"]:
        if field["fieldname"] == "spread_profile_id":
            assert field["fieldtype"] == "Link"
            assert field["options"] == "Spread Profile"
            break
    else:
        raise AssertionError("spread_profile_id field not found")


def test_budget_input_has_dim_fields():
    meta = _load_json("budget_input")
    fields = _get_field_names(meta)
    assert "dim_cost_center" in fields
    assert "dim_department" in fields


# --- Budget Input Python ---

def test_budget_input_py_has_validate():
    content = _load_py("budget_input")
    tree = ast.parse(content)
    methods = [n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]
    assert "validate" in methods


def test_budget_input_py_computes_annual():
    """validate must compute annual_amount as sum of periods."""
    content = _load_py("budget_input")
    assert "_compute_annual_amount" in content
    assert "annual_amount" in content


def test_budget_input_py_validates_layers():
    """Must enforce layer-based permissions."""
    content = _load_py("budget_input")
    assert "_validate_layer_permissions" in content
    assert "LAYER_ROLES" in content


def test_budget_input_py_has_layer_role_mapping():
    """Must map each layer to its role."""
    content = _load_py("budget_input")
    for layer in ["base", "challenge", "management", "board"]:
        assert f'"{layer}"' in content
    for role in ["Budget Submitter", "Budget Controller", "Budget Manager", "Budget Approver"]:
        assert role in content


def test_budget_input_py_ch_sync_on_approved():
    """Must only sync to CH when workflow_state == Approved."""
    content = _load_py("budget_input")
    assert "Approved" in content
    assert "sync_table" in content


def test_budget_input_py_has_spread_method():
    """Must have spread_annual method for top-down entry."""
    content = _load_py("budget_input")
    tree = ast.parse(content)
    methods = [n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]
    assert "spread_annual" in methods


def test_budget_input_has_workflow_state_field():
    meta = _load_json("budget_input")
    fields = _get_field_names(meta)
    assert "workflow_state" in fields


# --- Workflow ---

def test_budget_workflow_json_exists():
    path = os.path.join(APP_DIR, "epm", "doctype", "budget_input",
                        "budget_input_workflow.json")
    assert os.path.exists(path)


def test_budget_workflow_has_states():
    path = os.path.join(APP_DIR, "epm", "doctype", "budget_input",
                        "budget_input_workflow.json")
    with open(path) as f:
        wf = json.load(f)
    states = [s["state"] for s in wf["states"]]
    for s in ["Draft", "Submitted", "Approved", "Rejected"]:
        assert s in states, f"Missing state: {s}"


def test_budget_workflow_has_transitions():
    path = os.path.join(APP_DIR, "epm", "doctype", "budget_input",
                        "budget_input_workflow.json")
    with open(path) as f:
        wf = json.load(f)
    actions = [t["action"] for t in wf["transitions"]]
    assert "Approve" in actions
    assert "Reject" in actions


# --- JS ---

def test_budget_input_js_exists():
    path = os.path.join(APP_DIR, "epm", "doctype", "budget_input", "budget_input.js")
    assert os.path.exists(path)


def test_budget_input_js_has_spread_button():
    path = os.path.join(APP_DIR, "epm", "doctype", "budget_input", "budget_input.js")
    with open(path) as f:
        content = f.read()
    assert "Spread" in content
    assert "spread_annual" in content


# --- Roles (permissions) ---

def test_budget_input_has_budget_roles():
    meta = _load_json("budget_input")
    roles = [p["role"] for p in meta["permissions"]]
    for role in ["Budget Submitter", "Budget Controller", "Budget Manager", "Budget Approver"]:
        assert role in roles, f"Missing role: {role}"

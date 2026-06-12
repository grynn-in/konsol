"""TDD tests for config doctypes — Dimension, Measure, Fiscal Period."""
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


# --- Dimension ---

def test_dimension_json_exists():
    assert os.path.exists(os.path.join(APP_DIR, "epm", "doctype", "dimension", "dimension.json"))


def test_dimension_has_required_fields():
    meta = _load_json("dimension")
    fields = _get_field_names(meta)
    for f in ["dimension_name", "source_column", "label", "cube_type", "in_budget", "allocation_role"]:
        assert f in fields, f"Missing field: {f}"


def test_dimension_name_is_unique():
    meta = _load_json("dimension")
    for field in meta["fields"]:
        if field["fieldname"] == "dimension_name":
            assert field.get("unique") == 1


def test_dimension_autoname():
    meta = _load_json("dimension")
    assert meta["autoname"] == "field:dimension_name"


def test_dimension_imports_lifecycle():
    content = _load_py("dimension")
    assert "schema_lifecycle" in content


def test_dimension_no_on_update():
    """Saves are side-effect-free — no on_update hook."""
    content = _load_py("dimension")
    tree = ast.parse(content)
    methods = [n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]
    assert "on_update" not in methods


# --- Measure ---

def test_measure_json_exists():
    assert os.path.exists(os.path.join(APP_DIR, "epm", "doctype", "measure", "measure.json"))


def test_measure_has_required_fields():
    meta = _load_json("measure")
    fields = _get_field_names(meta)
    for f in ["measure_name", "expression", "label", "cube_type"]:
        assert f in fields, f"Missing field: {f}"


def test_measure_name_is_unique():
    meta = _load_json("measure")
    for field in meta["fields"]:
        if field["fieldname"] == "measure_name":
            assert field.get("unique") == 1


def test_measure_autoname():
    meta = _load_json("measure")
    assert meta["autoname"] == "field:measure_name"


def test_measure_imports_lifecycle():
    content = _load_py("measure")
    assert "schema_lifecycle" in content


def test_measure_cube_type_options():
    meta = _load_json("measure")
    for field in meta["fields"]:
        if field["fieldname"] == "cube_type":
            options = field["options"].split("\n")
            assert "sum" in options
            assert "count" in options
            assert "avg" in options


# --- Fiscal Period ---

def test_fiscal_period_json_exists():
    assert os.path.exists(os.path.join(APP_DIR, "epm", "doctype", "fiscal_period", "fiscal_period.json"))


def test_fiscal_period_has_required_fields():
    meta = _load_json("fiscal_period")
    fields = _get_field_names(meta)
    for f in ["fiscal_period", "label", "quarter", "half"]:
        assert f in fields, f"Missing field: {f}"


def test_fiscal_period_autoname():
    meta = _load_json("fiscal_period")
    assert "fiscal_period" in meta["autoname"]


def test_fiscal_period_triggers_regenerate():
    content = _load_py("fiscal_period")
    assert "regenerate_vars" in content


def test_fiscal_period_is_int():
    meta = _load_json("fiscal_period")
    for field in meta["fields"]:
        if field["fieldname"] == "fiscal_period":
            assert field["fieldtype"] == "Int"


def test_all_config_doctypes_module_epm():
    for dt in ["dimension", "measure", "fiscal_period"]:
        meta = _load_json(dt)
        assert meta["module"] == "EPM", f"{dt} not in EPM module"

"""TDD tests for Allocation Rule and Allocation Driver doctypes."""
import ast
import glob
import json
import os

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


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

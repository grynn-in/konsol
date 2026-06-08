"""TDD tests for Spread Profile doctype."""
import ast
import json
import os

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DT_DIR = os.path.join(APP_DIR, "epm", "doctype", "spread_profile")
JSON_PATH = os.path.join(DT_DIR, "spread_profile.json")
PY_PATH = os.path.join(DT_DIR, "spread_profile.py")


def test_doctype_json_exists():
    assert os.path.exists(JSON_PATH)


def test_has_required_fields():
    with open(JSON_PATH) as f:
        meta = json.load(f)
    fields = [f["fieldname"] for f in meta["fields"]]
    for required in ["profile_id", "profile_name", "fiscal_period", "weight"]:
        assert required in fields, f"Missing field: {required}"


def test_weight_is_float():
    with open(JSON_PATH) as f:
        meta = json.load(f)
    for field in meta["fields"]:
        if field["fieldname"] == "weight":
            assert field["fieldtype"] == "Float"


def test_fiscal_period_is_int():
    with open(JSON_PATH) as f:
        meta = json.load(f)
    for field in meta["fields"]:
        if field["fieldname"] == "fiscal_period":
            assert field["fieldtype"] == "Int"


def test_module_is_epm():
    with open(JSON_PATH) as f:
        meta = json.load(f)
    assert meta["module"] == "EPM"


def test_py_has_ch_sync():
    with open(PY_PATH) as f:
        content = f.read()
    assert "sync_doctype" in content
    assert "on_update" in content
    assert "on_trash" in content


def test_ch_table_is_spread_profiles():
    with open(PY_PATH) as f:
        content = f.read()
    assert "gold.spread_profiles" in content

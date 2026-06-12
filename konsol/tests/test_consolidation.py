"""TDD tests for Consolidation Group, IC Elimination Rule, Consolidation Adjustment."""
import ast
import glob
import json
import os

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _doctype_file(doctype_dir, ext):
    """Locate a doctype file in whichever konsol module owns it.

    Consolidation doctypes live under konsol/consolidation, not konsol/epm —
    resolve dynamically so the tests survive module reorganisation.
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


# --- Consolidation Group ---

def test_consolidation_group_json_exists():
    assert _doctype_file("consolidation_group", "json") is not None


def test_consolidation_group_has_required_fields():
    meta = _load_json("consolidation_group")
    fields = [f["fieldname"] for f in meta["fields"]]
    for f in ["consolidation_group", "data_area_id", "entity_name",
              "ownership_pct", "reporting_currency", "consolidation_method"]:
        assert f in fields, f"Missing field: {f}"


def test_consolidation_group_ownership_is_float():
    meta = _load_json("consolidation_group")
    for field in meta["fields"]:
        if field["fieldname"] == "ownership_pct":
            assert field["fieldtype"] == "Float"


def test_consolidation_group_method_options():
    meta = _load_json("consolidation_group")
    for field in meta["fields"]:
        if field["fieldname"] == "consolidation_method":
            options = field["options"].split("\n")
            assert "full" in options
            assert "proportional" in options
            assert "equity" in options


def test_consolidation_group_ch_sync():
    content = _load_py("consolidation_group")
    assert "sync_doctype" in content
    assert "gold.consolidation_groups" in content


# --- IC Elimination Rule ---

def test_ic_elimination_rule_json_exists():
    assert _doctype_file("ic_elimination_rule", "json") is not None


def test_ic_elimination_rule_has_required_fields():
    meta = _load_json("ic_elimination_rule")
    fields = [f["fieldname"] for f in meta["fields"]]
    for f in ["rule_id", "rule_name", "debit_account", "credit_account"]:
        assert f in fields, f"Missing field: {f}"


def test_ic_elimination_rule_id_unique():
    meta = _load_json("ic_elimination_rule")
    for field in meta["fields"]:
        if field["fieldname"] == "rule_id":
            assert field.get("unique") == 1


def test_ic_elimination_rule_has_entity_patterns():
    meta = _load_json("ic_elimination_rule")
    fields = [f["fieldname"] for f in meta["fields"]]
    assert "debit_entity_pattern" in fields
    assert "credit_entity_pattern" in fields


def test_ic_elimination_rule_ch_sync():
    content = _load_py("ic_elimination_rule")
    assert "sync_doctype" in content
    assert "gold.ic_elimination_rules" in content


# --- Consolidation Adjustment ---

def test_consolidation_adjustment_json_exists():
    assert _doctype_file("consolidation_adjustment", "json") is not None


def test_consolidation_adjustment_has_required_fields():
    meta = _load_json("consolidation_adjustment")
    fields = [f["fieldname"] for f in meta["fields"]]
    for f in ["consolidation_group", "adjustment_type", "journal_id", "data_area_id",
              "fiscal_year", "fiscal_period", "main_account", "debit_amount", "credit_amount"]:
        assert f in fields, f"Missing field: {f}"


def test_consolidation_adjustment_types():
    meta = _load_json("consolidation_adjustment")
    for field in meta["fields"]:
        if field["fieldname"] == "adjustment_type":
            options = field["options"].split("\n")
            assert "topside" in options
            assert "reclassification" in options


def test_consolidation_adjustment_has_posted_by():
    meta = _load_json("consolidation_adjustment")
    fields = [f["fieldname"] for f in meta["fields"]]
    assert "posted_by" in fields


def test_consolidation_adjustment_ch_sync():
    content = _load_py("consolidation_adjustment")
    assert "sync_doctype" in content
    assert "gold.consolidation_adjustments" in content


def test_all_consolidation_doctypes_module_consolidation():
    for dt in ["consolidation_group", "ic_elimination_rule", "consolidation_adjustment"]:
        meta = _load_json(dt)
        assert meta["module"] == "Consolidation", f"{dt} not in Consolidation module"

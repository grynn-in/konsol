"""Structural and unit tests for hierarchy_query (EPM v2)."""
import ast
import json
import os

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read(rel):
    with open(os.path.join(APP_DIR, rel)) as f:
        return f.read()


def test_hierarchy_query_module_exports():
    from konsol.hierarchy_query import (
        HIERARCHY_SCENARIO_CONFIG,
        batch_query_hierarchy,
        entity_is_wildcard,
        resolve_hierarchy_name,
        validate_hierarchy_read,
        validate_hierarchy_write,
    )
    assert "actuals" in HIERARCHY_SCENARIO_CONFIG
    assert "budget" in HIERARCHY_SCENARIO_CONFIG
    assert "variance" in HIERARCHY_SCENARIO_CONFIG
    assert callable(batch_query_hierarchy)
    assert callable(resolve_hierarchy_name)
    assert callable(validate_hierarchy_read)
    assert callable(validate_hierarchy_write)


def test_entity_wildcard():
    from konsol.hierarchy_query import entity_is_wildcard
    assert entity_is_wildcard("")
    assert entity_is_wildcard("*")
    assert entity_is_wildcard("ALL")
    assert entity_is_wildcard("all")
    assert not entity_is_wildcard("USMF")


def test_batch_query_hierarchy_no_duplicate_query_blocks():
    src = _read("hierarchy_query.py")
    assert src.count("def batch_query_hierarchy") == 1
    assert ".format(" not in src.split("def batch_query_hierarchy")[1]
    assert "sql_fixed" not in src


def test_api_wires_hierarchy_mode():
    src = _read("api.py")
    for fn in [
        "_extract_batch_dimensions",
        "_is_hierarchy_mode",
        "batch_query_hierarchy",
        "validate_hierarchy_read",
        "validate_hierarchy_write",
    ]:
        assert fn in src, f"Missing {fn} in api.py"


def test_epm_batch_splits_hierarchy_and_legacy():
    block = _read("api.py").split("def epm_batch")[1].split("\n@frappe.whitelist")[0]
    assert "hierarchy_valid" in block
    assert "legacy_valid" in block
    assert "batch_query_hierarchy" in block
    assert "_batch_query_clickhouse" in block


def test_epm_value_accepts_hierarchy_params():
    sig = _read("api.py").split("def epm_value")[1].split("):")[0]
    assert "hierarchy" in sig
    assert "node" in sig


def test_budget_cell_save_validates_hierarchy_leaf():
    block = _read("api.py").split("def budget_cell_save")[1].split("\n@frappe.whitelist")[0]
    assert "validate_hierarchy_write" in block


def test_excel_functions_json_has_hierarchy_params():
    meta = json.load(open(os.path.join(APP_DIR, "public", "excel-addin", "functions.json")))
    epm = next(f for f in meta["functions"] if f["id"] == "EPM")
    names = [p["name"] for p in epm["parameters"]]
    assert "hierarchy" in names
    assert "node" in names


def test_excel_functions_js_passes_hierarchy_node():
    src = _read(os.path.join("public", "excel-addin", "functions.js"))
    assert "req.hierarchy" in src
    assert "req.hierarchy_node" in src
    assert "data.hierarchy_node" in src


def test_batch_query_groups_by_entity():
    """Non-wildcard batches must include entity in the group key, not a shared filter."""
    src = _read("hierarchy_query.py")
    batch = src.split("def batch_query_hierarchy")[1].split("\ndef ")[0]
    assert 'wildcard else req.get("entity", "")' in batch
    assert "group_items[0][1].get(" not in batch
    assert "param_entity" not in batch


def test_legacy_cost_center_mapped_in_api():
    src = _read("api.py")
    assert "_LEGACY_DIM_MAP" in src
    assert "dim_cost_center" in src.split("_LEGACY_DIM_MAP")[1].split("def _resolve_and_validate")[0]
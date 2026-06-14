"""Structural tests for the Dimension Mapping doctype + seed regeneration.

Parse the doctype JSON / controller / dbt_config source without a live Frappe
site, mirroring test_connector_registry.py / test_fact_registry.py.
"""
import json
import os

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

ERP_TYPES = ["d365_fo", "d365_bc", "sap_s4", "sap_ecc", "sap_b1", "erpnext"]
# Must match the dbt seed columns (dimension_mappings.csv / dim_harmonize macro).
SEED_COLUMNS = ["dimension", "erp_source", "source_value", "canonical_value",
                "canonical_label", "status"]


def _doctype_json(name):
    with open(os.path.join(APP_DIR, "epm", "doctype", name, f"{name}.json")) as f:
        return json.load(f)


def _read(rel):
    with open(os.path.join(APP_DIR, rel)) as f:
        return f.read()


def _field(meta, fieldname):
    return next(f for f in meta["fields"] if f["fieldname"] == fieldname)


# --- doctype shape ---

def test_doctype_basics():
    meta = _doctype_json("dimension_mapping")
    assert meta["module"] == "EPM"
    assert meta["autoname"] == "hash"
    assert meta["issingle"] == 0 and meta["istable"] == 0


def test_required_fields_present():
    meta = _doctype_json("dimension_mapping")
    names = [f["fieldname"] for f in meta["fields"]]
    for f in ["dimension", "erp_source", "source_value", "canonical_value",
              "canonical_label", "status"]:
        assert f in names, f"Missing field: {f}"


def test_key_fields_are_required():
    meta = _doctype_json("dimension_mapping")
    for f in ["dimension", "erp_source", "source_value", "canonical_value"]:
        assert _field(meta, f).get("reqd") == 1, f"{f} should be reqd"


def test_dimension_is_link_to_dimension():
    dim = _field(_doctype_json("dimension_mapping"), "dimension")
    assert dim["fieldtype"] == "Link" and dim["options"] == "Dimension"


def test_erp_source_options_are_the_six_sources():
    meta = _doctype_json("dimension_mapping")
    assert _field(meta, "erp_source")["options"].split("\n") == ERP_TYPES


def test_status_lifecycle_options():
    opts = _field(_doctype_json("dimension_mapping"), "status")["options"].split("\n")
    assert opts == ["Draft", "Published", "Inactive"]


def test_permission_matrix():
    perms = {p["role"]: p for p in _doctype_json("dimension_mapping")["permissions"]}
    for role in ["System Manager", "EPM Admin"]:
        assert perms[role].get("write") and perms[role].get("delete")
    assert perms["EPM User"].get("read") and not perms["EPM User"].get("write")


# --- controller wiring ---

def test_controller_publish_regenerates_seed_and_rebuilds():
    src = _read(os.path.join("epm", "doctype", "dimension_mapping", "dimension_mapping.py"))
    assert "from konsol.dbt_config import regenerate_dimension_mappings_seed" in src
    assert "from konsol.schema_lifecycle import check_epm_admin, request_governed_rebuild" in src
    assert "def publish" in src and "def unpublish" in src
    assert "regenerate_dimension_mappings_seed()" in src
    assert "request_governed_rebuild(" in src
    # Uniqueness guard on the crosswalk key.
    assert "_validate_unique_key" in src


# --- seed writer ---

def test_dbt_config_writes_seed_with_correct_columns():
    src = _read("dbt_config.py")
    assert "def regenerate_dimension_mappings_seed" in src
    assert "dimension_mappings.csv" in src
    for col in SEED_COLUMNS:
        assert f'"{col}"' in src, f"seed column {col} missing from writer"
    # Only Published rows are written.
    assert 'filters={"status": "Published"}' in src


def test_request_governed_rebuild_skips_apply_schema():
    """The seed-only rebuild path must NOT call apply_schema (no DDL change)."""
    src = _read("schema_lifecycle.py")
    assert "def request_governed_rebuild" in src
    body = src.split("def request_governed_rebuild")[1].split("\ndef ")[0]
    assert "apply_schema" not in body


def test_dimension_mapping_is_a_fixture():
    assert '"Dimension Mapping"' in _read("hooks.py")

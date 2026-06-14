"""Structural tests for the Connector Registry (Phase 3).

Parse the doctype JSON / controllers / dbt_config source without a live Frappe
site, mirroring test_fact_registry.py / test_config_doctypes.py.
"""
import json
import os

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# The six erp_source values from the canonical staging schema.
ERP_TYPES = ["d365_fo", "d365_bc", "sap_s4", "sap_ecc", "sap_b1", "erpnext"]


def _pipeline_doctype_json(name):
    path = os.path.join(APP_DIR, "pipeline", "doctype", name, f"{name}.json")
    with open(path) as f:
        return json.load(f)


def _read(path):
    with open(path) as f:
        return f.read()


def _field_names(meta):
    return [f["fieldname"] for f in meta["fields"]]


def _field(meta, fieldname):
    return next(f for f in meta["fields"] if f["fieldname"] == fieldname)


# --- Connector doctype ---

def test_connector_doctype_basics():
    meta = _pipeline_doctype_json("connector")
    assert meta["module"] == "Pipeline"
    assert meta["autoname"] == "CONN-.#####"
    assert meta["issingle"] == 0 and meta["istable"] == 0


def test_connector_has_required_fields():
    fields = _field_names(_pipeline_doctype_json("connector"))
    for f in ["connector_name", "erp_type", "enabled", "airbyte_connection_id",
              "dbt_adapter_prefix", "legal_entities", "dimension_mappings",
              "last_sync_at", "last_sync_status", "last_sync_rows"]:
        assert f in fields, f"Missing field: {f}"


def test_erp_type_options_are_the_six_erp_sources():
    meta = _pipeline_doctype_json("connector")
    options = _field(meta, "erp_type")["options"].split("\n")
    assert options == ERP_TYPES


def test_child_tables_linked():
    meta = _pipeline_doctype_json("connector")
    assert _field(meta, "legal_entities")["options"] == "Connector Legal Entity"
    assert _field(meta, "dimension_mappings")["options"] == "Connector Dimension Map"


def test_connector_permission_matrix():
    perms = {p["role"]: p for p in _pipeline_doctype_json("connector")["permissions"]}
    for role in ["System Manager", "EPM Admin"]:
        assert perms[role].get("write") and perms[role].get("delete")
    assert perms["EPM Analyst"].get("read") and perms["EPM Analyst"].get("create")
    assert not perms["EPM Analyst"].get("write")
    assert perms["EPM User"].get("read") and not perms["EPM User"].get("create")


# --- Child tables ---

def test_legal_entity_child_table():
    meta = _pipeline_doctype_json("connector_legal_entity")
    assert meta["istable"] == 1
    assert _field(meta, "entity_id")["reqd"] == 1


def test_dimension_map_child_table():
    meta = _pipeline_doctype_json("connector_dimension_map")
    assert meta["istable"] == 1
    dim = _field(meta, "dimension")
    assert dim["fieldtype"] == "Link" and dim["options"] == "Dimension" and dim["reqd"] == 1
    assert _field(meta, "source_column")["reqd"] == 1


# --- erp_sources generation + controller wiring ---

def test_dbt_config_builds_erp_sources():
    src = _read(os.path.join(APP_DIR, "dbt_config.py"))
    assert "def _build_erp_sources_vars" in src
    assert 'new_vars["erp_sources"]' in src
    assert 'filters={"enabled": 1}' in src


def test_connector_controller_regenerates_vars():
    src = _read(os.path.join(APP_DIR, "pipeline", "doctype", "connector", "connector.py"))
    assert "from konsol.dbt_config import regenerate_vars" in src
    assert "def on_update" in src and "def on_trash" in src
    assert "regenerate_vars()" in src
    assert "dbt_adapter_prefix" in src


def test_connector_is_a_fixture():
    src = _read(os.path.join(APP_DIR, "hooks.py"))
    assert '"Connector"' in src

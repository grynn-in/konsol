"""Structural tests for the Fact Registry (Phase 2.3).

These parse the doctype JSON / controller / fixtures / schema_apply source
without a live Frappe site, mirroring test_config_doctypes.py.
"""
import ast
import json
import os

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _doctype_json(name):
    path = os.path.join(APP_DIR, "epm", "doctype", name, f"{name}.json")
    with open(path) as f:
        return json.load(f)


def _read(path):
    with open(path) as f:
        return f.read()


def _fixture(name):
    with open(os.path.join(APP_DIR, "fixtures", name)) as f:
        return json.load(f)


def _field_names(meta):
    return [f["fieldname"] for f in meta["fields"]]


# --- Fact Table doctype ---

def test_fact_table_has_new_fields():
    fields = _field_names(_doctype_json("fact_table"))
    for f in ["grain", "refresh_frequency", "generates_source", "extra_columns",
              "status", "fact_measures", "fact_dimensions"]:
        assert f in fields, f"Missing field: {f}"


def test_fact_table_measures_json_is_hidden_synced():
    """The legacy `measures` JSON stays for backward compat but is hidden/read-only."""
    meta = _doctype_json("fact_table")
    measures = next(f for f in meta["fields"] if f["fieldname"] == "measures")
    assert measures.get("read_only") == 1
    assert measures.get("hidden") == 1


def test_fact_table_status_options():
    meta = _doctype_json("fact_table")
    status = next(f for f in meta["fields"] if f["fieldname"] == "status")
    assert status["options"] == "Draft\nPublished\nInactive"


def test_fact_table_has_epm_admin_permission():
    meta = _doctype_json("fact_table")
    roles = {p["role"] for p in meta["permissions"]}
    assert "EPM Admin" in roles


def test_fact_measures_child_doctype():
    meta = _doctype_json("fact_table_measure")
    assert meta["istable"] == 1
    fields = _field_names(meta)
    assert "measure" in fields and "required" in fields


def test_fact_dimension_child_has_required():
    fields = _field_names(_doctype_json("fact_table_dimension"))
    assert "required" in fields


# --- Controller ---

def test_controller_publish_unpublish_and_lifecycle():
    content = _read(os.path.join(APP_DIR, "epm", "doctype", "fact_table", "fact_table.py"))
    assert "schema_lifecycle" in content
    tree = ast.parse(content)
    methods = [n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]
    for m in ["publish", "unpublish", "_validate_measures", "_validate_dimensions",
              "_validate_extra_columns", "_sync_json_fields"]:
        assert m in methods, f"Missing method: {m}"


def test_controller_no_on_update():
    content = _read(os.path.join(APP_DIR, "epm", "doctype", "fact_table", "fact_table.py"))
    tree = ast.parse(content)
    methods = [n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]
    assert "on_update" not in methods


# --- schema_apply generation ---

def test_schema_apply_has_fact_generation():
    content = _read(os.path.join(APP_DIR, "schema_apply.py"))
    assert "_apply_fact_tables" in content
    assert "_upsert_dbt_source" in content
    assert "facts_created" in content
    assert "sources_written" in content
    assert "CREATE TABLE IF NOT EXISTS" in content


# --- Fixture integrity (guards validation-vs-fixtures consistency) ---

def test_every_fact_measure_is_registered_published():
    published = {
        m["measure_name"] for m in _fixture("measure.json")
        if m.get("status") == "Published"
    }
    for fact in _fixture("fact_table.json"):
        for row in fact.get("fact_measures", []):
            assert row["measure"] in published, (
                f"{fact['fact_name']} references unregistered measure {row['measure']}"
            )


def test_every_fact_dimension_is_registered_published():
    published = {
        d["dimension_name"] for d in _fixture("dimension.json")
        if d.get("status") == "Published"
    }
    for fact in _fixture("fact_table.json"):
        for row in fact.get("fact_dimensions", []):
            assert row["dimension"] in published, (
                f"{fact['fact_name']} references unregistered dimension {row['dimension']}"
            )


def test_fact_fixtures_use_child_table_and_published():
    for fact in _fixture("fact_table.json"):
        assert fact.get("status") == "Published", f"{fact['fact_name']} not Published"
        assert "fact_measures" in fact, f"{fact['fact_name']} missing fact_measures child"


def test_generates_source_facts_target_staging_schema():
    for fact in _fixture("fact_table.json"):
        if fact.get("generates_source"):
            assert fact["clickhouse_table"].startswith("epm_staging."), (
                f"{fact['fact_name']} write-back fact must live in epm_staging"
            )


def test_extra_columns_is_valid_json_when_present():
    for fact in _fixture("fact_table.json"):
        ec = fact.get("extra_columns")
        if ec:
            parsed = json.loads(ec)
            assert isinstance(parsed, list)
            for col in parsed:
                assert "name" in col

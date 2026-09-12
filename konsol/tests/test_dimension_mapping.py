"""Structural tests for the Dimension Mapping doctype + seed regeneration.

Parse the doctype JSON / controller / dbt_config source without a live Frappe
site, mirroring test_connector_registry.py / test_fact_registry.py.
"""
import json
import os

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

ERP_TYPES = ["d365_fo", "d365_bc", "sap_s4", "sap_ecc", "sap_b1", "erpnext"]
# Must match the dbt seed columns (dimension_mappings.csv / dim_harmonize macro).
SEED_COLUMNS = ["dimension", "erp_source", "entity", "source_value", "canonical_value",
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

def test_controller_publish_syncs_warehouse_and_rebuilds():
    """F3: publish writes through to epm_staging (Published rows only); the CSV
    seed writer is gone. The lifecycle itself lives in the shared base class —
    six hand-copied sync calls across two controllers is what let publish() and
    reconcile_all() disagree about Draft rows."""
    src = _read(os.path.join("epm", "doctype", "dimension_mapping", "dimension_mapping.py"))
    assert "from konsol.governed_reference import GovernedReferenceDocument" in src
    assert "class DimensionMapping(GovernedReferenceDocument)" in src
    assert 'CH_TABLE = "epm_staging.dimension_mappings"' in src
    assert 'CH_SYNC_FILTERS = {"status": "Published"}' in src
    assert "regenerate_dimension_mappings_seed" not in src
    # Uniqueness guard on the crosswalk key.
    assert "_validate_unique_key" in src
    # The controller must NOT carry its own copy of the sync/lifecycle.
    for copied in ("sync_doctype_filtered(", "def publish(", "def unpublish(",
                   "request_governed_rebuild("):
        assert copied not in src, f"{copied} should come from the base class"


def test_blank_entity_duplicate_guard_matches_null():
    """A blank Link is stored as NULL, not '' — so `{"entity": ""}` matched
    nothing and the uniqueness guard was inert for ERP-wide defaults, the rows
    that fan the dim_harmonize _dflt join out when duplicated.

    It must be `["is", "not set"]`. `["in", ["", None]]` compiles to
    `entity IN ('', NULL)`, and SQL never matches NULL through IN — verified on
    the running site, where it accepted a duplicate ERP-wide default.
    """
    src = _read(os.path.join("epm", "doctype", "dimension_mapping", "dimension_mapping.py"))
    # code only — the docstring names the wrong spelling as a counter-example
    code = src.split("def _validate_unique_key")[1].split('"""')[2]
    assert '["is", "not set"]' in code
    assert '["in", ["", None]]' not in code
    assert '"entity": self.entity or ""' not in code


# --- seed writer ---

def test_field_map_carries_the_crosswalk_columns():
    """F3: the columns the dbt side reads now travel via the CH_FIELD_MAP."""
    src = _read(os.path.join("epm", "doctype", "dimension_mapping", "dimension_mapping.py"))
    for col in SEED_COLUMNS:
        assert f'"{col}"' in src, f"crosswalk column {col} missing from field map"


def test_request_governed_rebuild_skips_apply_schema():
    """The seed-only rebuild path must NOT call apply_schema (no DDL change)."""
    src = _read("schema_lifecycle.py")
    assert "def request_governed_rebuild" in src
    body = src.split("def request_governed_rebuild")[1].split("\ndef ")[0]
    assert "apply_schema" not in body


def test_dimension_mapping_is_not_a_fixture():
    """A dimension mapping maps one site's ERP values to canonical ones. The
    shipped rows mapped the Contoso demo's D365 cost centres, and a fixture is
    force-reimported on every migrate, so a site's own mappings would be
    overwritten by another company's."""
    src = _read("hooks.py")
    fixtures = src.split("fixtures = [")[1].split("]")[0]
    entries = [l.strip() for l in fixtures.splitlines() if l.strip().startswith('"')]
    assert '"Dimension Mapping",' not in entries


def test_after_delete_resyncs_not_on_trash():
    """Deleting a Published mapping must re-sync the warehouse — via
    after_delete, NOT on_trash (on_trash runs before the row is removed, so the
    TRUNCATE+INSERT would still include the deleted doc). Now inherited, so the
    contract is asserted where it lives."""
    src = _read("governed_reference.py")
    assert "def after_delete" in src
    body = src.split("def after_delete")[1]
    assert "self._resync()" in body
    assert "_request_rebuild(" in body
    # Must NOT use on_trash (wrong timing for this).
    assert "def on_trash" not in src
    controller = _read(
        os.path.join("epm", "doctype", "dimension_mapping", "dimension_mapping.py"))
    assert "def on_trash" not in controller


def test_after_migrate_reconciles_the_warehouse():
    """Fixture import doesn't run publish(), so after_migrate must re-sync the
    crosswalk — which now happens through the generic ClickHouse reconcile, not
    a per-seed regenerator (F3: no CSV writers remain in install.py)."""
    src = _read("install.py")
    assert "regenerate_dimension_mappings_seed" not in src
    after = src.split("def after_migrate")[1].split("\ndef ")[0]
    assert "_reconcile_clickhouse()" in after

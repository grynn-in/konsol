"""Structural tests for Connector Health (Phase 3 — Scale Architecture).

Parse the doctype JSON / controller / hooks / api source without a live Frappe
site, mirroring test_connector_registry.py.
"""
import json
import os

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _pipeline_doctype_json(name):
    path = os.path.join(APP_DIR, "pipeline", "doctype", name, f"{name}.json")
    with open(path) as f:
        return json.load(f)


def _read(*parts):
    with open(os.path.join(APP_DIR, *parts)) as f:
        return f.read()


def _field_names(meta):
    return [f["fieldname"] for f in meta["fields"]]


def _field(meta, fieldname):
    return next(f for f in meta["fields"] if f["fieldname"] == fieldname)


# --- doctype ---

def test_connector_health_doctype_basics():
    meta = _pipeline_doctype_json("connector_health")
    assert meta["module"] == "Pipeline"
    assert meta["autoname"] == "field:connector"  # one row per connector
    assert meta["issingle"] == 0 and meta["istable"] == 0


def test_connector_health_required_fields():
    fields = _field_names(_pipeline_doctype_json("connector_health"))
    for f in ["connector", "erp_source", "last_sync_status", "lag_minutes",
              "entities_loaded", "rows_emitted",
              "last_sync_end", "last_error", "checked_at"]:
        assert f in fields, f"Missing field: {f}"


def test_connector_link_is_unique_and_required():
    meta = _pipeline_doctype_json("connector_health")
    conn = _field(meta, "connector")
    assert conn["fieldtype"] == "Link" and conn["options"] == "Connector"
    assert conn["reqd"] == 1 and conn["unique"] == 1


def test_status_options_are_the_health_states():
    meta = _pipeline_doctype_json("connector_health")
    options = _field(meta, "last_sync_status")["options"].split("\n")
    assert options == ["Never", "Running", "Succeeded", "Failed", "Stale"]


def test_key_fields_in_list_view():
    meta = _pipeline_doctype_json("connector_health")
    for f in ["connector", "last_sync_status", "lag_minutes", "entities_loaded"]:
        assert _field(meta, f).get("in_list_view") == 1


def test_connector_health_permission_matrix():
    perms = {p["role"]: p for p in _pipeline_doctype_json("connector_health")["permissions"]}
    assert perms["System Manager"].get("write") and perms["System Manager"].get("delete")
    # Derived doctype: non-admin roles read-only (no write/create).
    for role in ["EPM Admin", "EPM Analyst", "EPM User"]:
        assert perms[role].get("read") and not perms[role].get("write")
        assert not perms[role].get("create")


# --- Connector gains the staleness threshold ---

def test_connector_has_sync_frequency_minutes():
    meta = _pipeline_doctype_json("connector")
    f = _field(meta, "sync_frequency_minutes")
    assert f["fieldtype"] == "Int" and f["default"] == "1440"


# --- controller / scheduler logic ---

def test_controller_derives_status_and_alerts_on_transition():
    src = _read("pipeline", "doctype", "connector_health", "connector_health.py")
    assert "def refresh_connector_health" in src
    # status mapping + the unhealthy set used for both Stale detection and alerts
    assert '"Success": "Succeeded"' in src
    assert "_UNHEALTHY" in src
    # alert only on the transition INTO an unhealthy state (no repeat spam)
    assert "prev_status not in _UNHEALTHY" in src
    # entities_loaded comes from the canonical staging table, best-effort
    assert "epm_staging.stg_gl_entries" in src
    # never run during install/migrate/patch
    assert "in_install" in src and "in_migrate" in src


def test_scheduler_event_registered():
    src = _read("hooks.py")
    assert "scheduler_events" in src
    assert "*/5 * * * *" in src
    assert "connector_health.refresh_connector_health" in src


def test_api_endpoint_is_whitelisted_and_gated():
    src = _read("api.py")
    assert "def connector_health" in src
    assert 'frappe.has_permission("Connector Health", "read")' in src


def test_list_view_indicator_exists():
    src = _read("pipeline", "doctype", "connector_health", "connector_health_list.js")
    assert "get_indicator" in src
    for status in ["Succeeded", "Failed", "Stale", "Running", "Never"]:
        assert status in src

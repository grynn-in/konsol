"""PRD-17 — resource resolver (pure core).

Host tests for the pure :mod:`konsol.orchestrator.resources` module: the
``Resource`` dataclass and ``resolve(kind, params, settings)`` which picks a
connection from run ``params`` first, else from the ``settings`` (EPM Settings
defaults) dict, validating the required keys per kind and raising a precise
``ValueError`` naming the kind + missing key. No frappe at top level. The
frappe-bound getters / whitelisted ``list_resources`` are guarded with
``importorskip``.
"""
import os

import pytest

from konsol.orchestrator import resources


ORCH_DIR = os.path.dirname(resources.__file__)


# ---- purity / no top-level frappe ------------------------------------------

def test_resources_module_has_no_toplevel_frappe_import():
    with open(os.path.join(ORCH_DIR, "resources.py")) as fh:
        src = fh.read()
    for line in src.splitlines():
        if line.startswith("import frappe") or line.startswith("from frappe"):
            raise AssertionError("resources.py must not import frappe at top level")


# ---- Resource dataclass ----------------------------------------------------

def test_resource_dataclass_fields():
    r = resources.Resource(kind="dbt", name="x", config={"project_dir": "/p"})
    assert r.kind == "dbt"
    assert r.name == "x"
    assert r.config == {"project_dir": "/p"}


# ---- resolve: complete settings -> Resource --------------------------------

def test_resolve_airbyte_from_settings():
    settings = {
        "workspace_id": "ws1",
        "connection_id": "conn1",
        "destination_id": "dest1",
    }
    r = resources.resolve("airbyte", {}, settings)
    assert isinstance(r, resources.Resource)
    assert r.kind == "airbyte"
    assert r.config["workspace_id"] == "ws1"
    assert r.config["connection_id"] == "conn1"
    assert r.config["destination_id"] == "dest1"


def test_resolve_dbt_from_settings():
    r = resources.resolve("dbt", {}, {"project_dir": "/home/dbt"})
    assert r.kind == "dbt"
    assert r.config["project_dir"] == "/home/dbt"


def test_resolve_clickhouse_from_settings():
    r = resources.resolve("clickhouse", {}, {"host": "ch", "db": "epm_gold"})
    assert r.kind == "clickhouse"
    assert r.config["host"] == "ch"
    assert r.config["db"] == "epm_gold"


# ---- resolve: missing keys -> clear ValueError -----------------------------

def test_resolve_airbyte_missing_key_names_kind_and_key():
    with pytest.raises(ValueError) as exc:
        resources.resolve("airbyte", {}, {"workspace_id": "ws1", "connection_id": "c1"})
    msg = str(exc.value)
    assert "airbyte" in msg
    assert "destination_id" in msg


def test_resolve_dbt_missing_all():
    with pytest.raises(ValueError) as exc:
        resources.resolve("dbt", {}, {})
    msg = str(exc.value)
    assert "dbt" in msg
    assert "project_dir" in msg


def test_resolve_clickhouse_missing_db():
    with pytest.raises(ValueError) as exc:
        resources.resolve("clickhouse", {}, {"host": "ch"})
    msg = str(exc.value)
    assert "clickhouse" in msg
    assert "db" in msg


def test_resolve_unknown_kind_raises():
    with pytest.raises(ValueError) as exc:
        resources.resolve("redis", {}, {})
    assert "redis" in str(exc.value)


# ---- resolve: precedence params > settings ---------------------------------

def test_resolve_params_override_settings():
    settings = {
        "workspace_id": "ws-default",
        "connection_id": "conn-default",
        "destination_id": "dest-default",
    }
    params = {"connection_id": "conn-override"}
    r = resources.resolve("airbyte", params, settings)
    assert r.config["connection_id"] == "conn-override"
    # other keys still fall back to settings
    assert r.config["workspace_id"] == "ws-default"
    assert r.config["destination_id"] == "dest-default"


def test_resolve_params_fill_missing_setting():
    settings = {"host": "ch"}
    params = {"db": "epm_gold"}
    r = resources.resolve("clickhouse", params, settings)
    assert r.config["host"] == "ch"
    assert r.config["db"] == "epm_gold"


def test_resolve_empty_string_treated_as_missing():
    with pytest.raises(ValueError):
        resources.resolve("dbt", {"project_dir": ""}, {"project_dir": ""})


def test_resolve_tolerates_none_params_and_settings():
    with pytest.raises(ValueError):
        resources.resolve("dbt", None, None)


# ---- frappe-bound getters (guarded) ----------------------------------------

def test_getters_and_api_exist_and_callable():
    assert callable(resources.airbyte_resource)
    assert callable(resources.dbt_resource)
    assert callable(resources.clickhouse_resource)
    assert callable(resources.list_resources)
    # __name__ preserved through the whitelist decorator
    assert resources.list_resources.__name__ == "list_resources"


def test_frappe_getters_smoke():
    frappe = pytest.importorskip("frappe")  # noqa: F841 - skips on host
    # In a bench these read EPM Settings; here we only assert importability.
    assert callable(resources.airbyte_resource)

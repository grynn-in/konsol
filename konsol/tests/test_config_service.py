"""Tests for konsol.config_service and konsol.cli_api."""
import ast
import importlib
import os
import sys
from unittest.mock import MagicMock

import pytest

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read(rel):
    with open(os.path.join(APP_DIR, rel)) as f:
        return f.read()


def _func_names(path):
    with open(path) as f:
        tree = ast.parse(f.read())
    return [n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]


# --- module shape ---

def test_config_service_module_exists():
    assert os.path.exists(os.path.join(APP_DIR, "config_service.py"))


def test_config_service_exposes_list_functions():
    names = _func_names(os.path.join(APP_DIR, "config_service.py"))
    assert "list_dimensions" in names
    assert "get_dimension" in names
    assert "upsert_dimension" in names
    assert "publish_dimension" in names
    assert "get_measure" in names
    assert "upsert_measure" in names
    assert "publish_measure" in names
    assert "apply_schema" in names
    assert "get_schema_status" in names
    assert "list_measures" in names


def test_cli_api_module_exists():
    assert os.path.exists(os.path.join(APP_DIR, "cli_api.py"))


def test_cli_api_whitelists_list_endpoints():
    content = _read("cli_api.py")
    assert "@frappe.whitelist()" in content
    assert "list_dimensions_api" in content
    assert "get_dimension_api" in content
    assert "upsert_dimension_api" in content
    assert "publish_dimension_api" in content
    assert "get_measure_api" in content
    assert "upsert_measure_api" in content
    assert "publish_measure_api" in content
    assert "apply_schema_api" in content
    assert "get_schema_status_api" in content
    assert "list_measures_api" in content
    assert "from konsol.config_service import" in content


def test_config_service_queries_dimension_doctype():
    content = _read("config_service.py")
    assert '"Dimension"' in content
    assert "dimension_name" in content


def test_config_service_queries_measure_doctype():
    content = _read("config_service.py")
    assert '"Measure"' in content
    assert "measure_name" in content


# --- behavior with mocked frappe ---

@pytest.fixture
def config_service(monkeypatch):
    fake_frappe = MagicMock()
    monkeypatch.setitem(sys.modules, "frappe", fake_frappe)
    if "konsol.config_service" in sys.modules:
        del sys.modules["konsol.config_service"]
    module = importlib.import_module("konsol.config_service")
    return module, fake_frappe


def test_list_dimensions_returns_serialized_rows(config_service):
    module, fake_frappe = config_service
    fake_frappe.get_all.return_value = [
        {
            "name": "dim_cost_center",
            "dimension_name": "dim_cost_center",
            "source_column": "CostCenter",
            "label": "Cost Center",
            "cube_type": "string",
            "in_budget": 1,
            "allocation_role": "cost_center",
            "permission_doctype": None,
            "status": "Published",
        }
    ]

    rows = module.list_dimensions()

    fake_frappe.get_all.assert_called_once()
    call = fake_frappe.get_all.call_args
    assert call.args[0] == "Dimension"
    assert call.kwargs["order_by"] == "dimension_name asc"
    assert rows[0]["in_budget"] is True
    assert rows[0]["dimension_name"] == "dim_cost_center"


def test_list_dimensions_honors_status_filter(config_service):
    module, fake_frappe = config_service
    fake_frappe.get_all.return_value = []

    module.list_dimensions({"status": "Published"})

    call = fake_frappe.get_all.call_args
    assert call.kwargs["filters"] == {"status": "Published"}


def test_list_measures_returns_rows(config_service):
    module, fake_frappe = config_service
    fake_frappe.get_all.return_value = [
        {
            "name": "period_net_amount",
            "measure_name": "period_net_amount",
            "expression": "sum(accounting_currency_amount)",
            "label": "Net Amount",
            "cube_type": "sum",
            "status": "Published",
        }
    ]

    rows = module.list_measures()

    fake_frappe.get_all.assert_called_once()
    call = fake_frappe.get_all.call_args
    assert call.args[0] == "Measure"
    assert rows[0]["measure_name"] == "period_net_amount"


def test_get_dimension_returns_doc(config_service):
    module, fake_frappe = config_service
    doc = MagicMock()
    doc.name = "dim_project"
    doc.dimension_name = "dim_project"
    doc.source_column = "Project"
    doc.label = "Project"
    doc.cube_type = "string"
    doc.in_budget = 0
    doc.allocation_role = None
    doc.permission_doctype = None
    doc.status = "Draft"
    fake_frappe.db.exists.return_value = True
    fake_frappe.get_doc.return_value = doc

    row = module.get_dimension("dim_project")

    fake_frappe.get_doc.assert_called_once_with("Dimension", "dim_project")
    assert row["dimension_name"] == "dim_project"
    assert row["status"] == "Draft"
    assert row["in_budget"] is False


def test_upsert_dimension_creates_draft(config_service):
    module, fake_frappe = config_service
    doc = MagicMock()
    doc.dimension_name = "dim_project"
    doc.name = "dim_project"
    doc.source_column = "Project"
    doc.label = "Project"
    doc.cube_type = "string"
    doc.in_budget = 0
    doc.allocation_role = None
    doc.permission_doctype = None
    doc.status = "Draft"
    fake_frappe.db.exists.return_value = False
    fake_frappe.new_doc.return_value = doc

    result = module.upsert_dimension(
        {
            "dimension_name": "dim_project",
            "source_column": "Project",
            "label": "Project",
        }
    )

    fake_frappe.new_doc.assert_called_once_with("Dimension")
    doc.save.assert_called_once()
    fake_frappe.db.commit.assert_called_once()
    doc.publish.assert_not_called()
    doc.reload.assert_called_once()
    assert result["created"] is True
    assert result["published"] is False


def test_upsert_dimension_publish_calls_controller(config_service):
    module, fake_frappe = config_service
    doc = MagicMock()
    doc.dimension_name = "dim_project"
    doc.name = "dim_project"
    doc.source_column = "Project"
    doc.label = "Project"
    doc.cube_type = "string"
    doc.in_budget = 0
    doc.allocation_role = None
    doc.permission_doctype = None
    doc.status = "Published"
    fake_frappe.db.exists.return_value = False
    fake_frappe.new_doc.return_value = doc

    result = module.upsert_dimension(
        {
            "dimension_name": "dim_project",
            "source_column": "Project",
            "label": "Project",
        },
        publish=True,
    )

    doc.publish.assert_called_once()
    assert result["published"] is True


def test_publish_dimension_delegates_to_doc(config_service):
    module, fake_frappe = config_service
    doc = MagicMock()
    doc.name = "dim_project"
    doc.dimension_name = "dim_project"
    doc.source_column = "Project"
    doc.label = "Project"
    doc.cube_type = "string"
    doc.in_budget = 0
    doc.allocation_role = None
    doc.permission_doctype = None
    doc.status = "Published"
    fake_frappe.db.exists.return_value = True
    fake_frappe.get_doc.return_value = doc

    result = module.publish_dimension("dim_project")

    doc.publish.assert_called_once()
    doc.reload.assert_called_once()
    assert result["published"] is True
    assert result["dimension"]["status"] == "Published"


def test_get_measure_returns_doc(config_service):
    module, fake_frappe = config_service
    doc = MagicMock()
    doc.name = "period_headcount"
    doc.measure_name = "period_headcount"
    doc.expression = "sum(headcount)"
    doc.label = "Headcount"
    doc.cube_type = "sum"
    doc.status = "Draft"
    fake_frappe.db.exists.return_value = True
    fake_frappe.get_doc.return_value = doc

    row = module.get_measure("period_headcount")

    fake_frappe.get_doc.assert_called_once_with("Measure", "period_headcount")
    assert row["measure_name"] == "period_headcount"
    assert row["status"] == "Draft"


def test_upsert_measure_creates_draft(config_service):
    module, fake_frappe = config_service
    doc = MagicMock()
    doc.measure_name = "period_headcount"
    doc.name = "period_headcount"
    doc.expression = "sum(headcount)"
    doc.label = "Headcount"
    doc.cube_type = "sum"
    doc.status = "Draft"
    fake_frappe.db.exists.return_value = False
    fake_frappe.new_doc.return_value = doc

    result = module.upsert_measure(
        {
            "measure_name": "period_headcount",
            "expression": "sum(headcount)",
            "label": "Headcount",
        }
    )

    fake_frappe.new_doc.assert_called_once_with("Measure")
    doc.save.assert_called_once()
    doc.publish.assert_not_called()
    assert result["created"] is True
    assert result["published"] is False


def test_publish_measure_delegates_to_doc(config_service):
    module, fake_frappe = config_service
    doc = MagicMock()
    doc.name = "period_headcount"
    doc.measure_name = "period_headcount"
    doc.expression = "sum(headcount)"
    doc.label = "Headcount"
    doc.cube_type = "sum"
    doc.status = "Published"
    fake_frappe.db.exists.return_value = True
    fake_frappe.get_doc.return_value = doc

    result = module.publish_measure("period_headcount")

    doc.publish.assert_called_once()
    assert result["published"] is True
    assert result["measure"]["status"] == "Published"


def test_apply_schema_delegates_to_schema_apply(config_service, monkeypatch):
    module, _fake_frappe = config_service
    called = {}

    def fake_apply_schema(run_dbt=False):
        called["run_dbt"] = run_dbt
        return {"vars_updated": True, "errors": []}

    monkeypatch.setitem(
        sys.modules,
        "konsol.schema_apply",
        MagicMock(apply_schema=fake_apply_schema),
    )

    result = module.apply_schema(run_dbt=True)

    assert called["run_dbt"] is True
    assert result["vars_updated"] is True


def test_get_schema_status_aggregates_registry_and_builds(config_service):
    module, fake_frappe = config_service
    pbr_calls = []

    def fake_get_all(doctype, **kwargs):
        if doctype == "Dimension":
            return [{"status": "Published"}, {"status": "Draft"}]
        if doctype == "Measure":
            return [{"status": "Published"}]
        if doctype == "Fact Table":
            return [{"status": "Published"}]
        if doctype == "Pipeline Build Request":
            pbr_calls.append(kwargs)
            if len(pbr_calls) == 1:
                return [
                    {
                        "name": "PBR-00001",
                        "build_scope": "full",
                        "workflow_state": "Approved",
                    }
                ]
            return [
                {
                    "name": "PBR-00000",
                    "build_scope": "full",
                    "workflow_state": "Completed",
                }
            ]
        return []

    fake_frappe.get_all.side_effect = fake_get_all
    fake_frappe.db.table_exists.return_value = True

    status = module.get_schema_status()

    assert status["registry"]["dimensions"]["Published"] == 1
    assert status["registry"]["dimensions"]["Draft"] == 1
    assert status["registry"]["measures"]["Published"] == 1
    assert status["registry"]["fact_tables"]["Published"] == 1
    assert status["pending_builds"][0]["name"] == "PBR-00001"
    assert status["recent_builds"][0]["name"] == "PBR-00000"
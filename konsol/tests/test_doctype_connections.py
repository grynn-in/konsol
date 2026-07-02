"""Site-free checks that key doctypes expose Frappe Connections dashboards."""
import os
import sys

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PKG_ROOT = os.path.dirname(APP_DIR)
if PKG_ROOT not in sys.path:
    sys.path.insert(0, PKG_ROOT)


def _load_dashboard(relative_path):
    path = os.path.join(APP_DIR, relative_path)
    ns = {"_": lambda s: s}
    with open(path) as fh:
        exec(fh.read(), ns)  # noqa: S102 — frappe-free dashboard modules
    return ns["get_data"]()


def _items(data):
    return [item for group in data.get("transactions", []) for item in group.get("items", [])]


def test_budget_cycle_links_sheets():
    data = _load_dashboard("epm/doctype/budget_cycle/budget_cycle_dashboard.py")
    assert "Budget Sheet" in _items(data)
    assert data["non_standard_fieldnames"]["Budget Sheet"] == "cycle"


def test_budget_sheet_links_cycle():
    data = _load_dashboard("epm/doctype/budget_sheet/budget_sheet_dashboard.py")
    assert data["internal_links"]["Budget Cycle"] == "cycle"


def test_scenario_links_budget_cycles():
    data = _load_dashboard("epm/doctype/scenario_definition/scenario_definition_dashboard.py")
    assert "Budget Cycle" in _items(data)


def test_reporting_hierarchy_links_members():
    data = _load_dashboard(
        "epm/doctype/reporting_hierarchy/reporting_hierarchy_dashboard.py")
    assert "Reporting Hierarchy Member" in _items(data)


def test_dimension_links_mappings_and_hierarchies():
    data = _load_dashboard("epm/doctype/dimension/dimension_dashboard.py")
    items = _items(data)
    assert "Dimension Mapping" in items
    assert "Reporting Hierarchy" in items


def test_dataset_internal_registry_links():
    data = _load_dashboard("epm/doctype/dataset/dataset_dashboard.py")
    assert data["internal_links"]["Measure"] == ["fact_measures", "measure"]
    assert data["internal_links"]["Dimension"] == ["fact_dimensions", "dimension"]


def test_measure_links_datasets_with_custom_count():
    data = _load_dashboard("epm/doctype/measure/measure_dashboard.py")
    assert "Dataset" in _items(data)
    assert data["method"] == "konsol.desk.connections.get_open_count"


def test_connector_links_health_and_dims():
    data = _load_dashboard("pipeline/doctype/connector/connector_dashboard.py")
    assert "Connector Health" in _items(data)
    assert data["internal_links"]["Dimension"] == ["dimension_mappings", "dimension"]


def test_connector_health_links_connector():
    data = _load_dashboard("pipeline/doctype/connector_health/connector_health_dashboard.py")
    assert data["internal_links"]["Connector"] == "connector"


def test_build_model_links_build_scope():
    data = _load_dashboard("pipeline/doctype/build_model/build_model_dashboard.py")
    assert data["internal_links"]["Build Scope"] == "build_domain"


def test_build_scope_links_build_models():
    data = _load_dashboard("pipeline/doctype/build_scope/build_scope_dashboard.py")
    assert "Build Model" in _items(data)
    assert data["non_standard_fieldnames"]["Build Model"] == "build_domain"


def test_pipeline_run_links_period_closes():
    data = _load_dashboard("pipeline/doctype/pipeline_run/pipeline_run_dashboard.py")
    assert "Period Close" in _items(data)
    assert data["non_standard_fieldnames"]["Period Close"] == "pipeline_run"
    assert data["internal_links"]["Build Approval"] == "pipeline_build_request"


def test_pipeline_run_links_build_approval():
    data = _load_dashboard("pipeline/doctype/pipeline_run/pipeline_run_dashboard.py")
    assert "Build Approval" in _items(data)


def test_period_close_links_pipeline_run():
    data = _load_dashboard("consolidation/doctype/period_close/period_close_dashboard.py")
    assert data["internal_links"]["Pipeline Run"] == "pipeline_run"


def test_consolidation_group_links_children():
    data = _load_dashboard(
        "consolidation/doctype/consolidation_group/consolidation_group_dashboard.py")
    items = _items(data)
    assert "Ownership Period" in items
    assert "Historical Equity Rate" in items
    assert "Consolidation Adjustment" in items
    assert data["method"] == "konsol.desk.connections.get_open_count"


def test_allocation_rule_links_drivers():
    data = _load_dashboard("allocation/doctype/allocation_rule/allocation_rule_dashboard.py")
    assert "Allocation Driver" in _items(data)
    assert data["method"] == "konsol.desk.connections.get_open_count"


def test_build_approval_dashboard_links_runs_and_trigger_js():
    data = _load_dashboard(
        "pipeline/doctype/build_approval/build_approval_dashboard.py")
    assert "Pipeline Run" in _items(data)
    assert data["non_standard_fieldnames"]["Pipeline Run"] == "pipeline_build_request"
    assert data["method"] == "konsol.desk.connections.get_open_count"
    js_path = os.path.join(
        APP_DIR, "pipeline/doctype/build_approval/build_approval.js")
    js = open(js_path).read()
    assert "refresh_build_approval_connections" in js
    assert "frm.dashboard.hide()" not in js


def test_pipeline_build_request_trigger_helper():
    from konsol.desk.connection_filters import pipeline_build_request_trigger

    assert pipeline_build_request_trigger("Allocation Run", "ARUN-1") == (
        "Allocation Run",
        ["ARUN-1"],
    )
    assert pipeline_build_request_trigger(None, None) == (None, [])


def test_connection_filter_helpers():
    from konsol.desk.connection_filters import (
        allocation_driver_filters,
        consolidation_group_child_filters,
    )

    assert consolidation_group_child_filters("AMGRP") == {"consolidation_group": "AMGRP"}
    assert consolidation_group_child_filters("AMGRP", "AMHQ") == {
        "consolidation_group": "AMGRP",
        "data_area_id": "AMHQ",
    }
    assert allocation_driver_filters("headcount") == {"driver_type": "headcount"}
    assert allocation_driver_filters("headcount", "CC100") == {
        "driver_type": "headcount",
        "cost_center": "CC100",
    }
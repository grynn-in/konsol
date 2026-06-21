"""Site-free checks that key doctypes expose ERPNext-style Connections dashboards."""
import os

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


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


def test_fact_table_internal_registry_links():
    data = _load_dashboard("epm/doctype/fact_table/fact_table_dashboard.py")
    assert data["internal_links"]["Measure"] == ["fact_measures", "measure"]
    assert data["internal_links"]["Dimension"] == ["fact_dimensions", "dimension"]


def test_connector_links_health_and_dims():
    data = _load_dashboard("pipeline/doctype/connector/connector_dashboard.py")
    assert "Connector Health" in _items(data)
    assert data["internal_links"]["Dimension"] == ["dimension_mappings", "dimension"]


def test_close_run_links_pipeline_run():
    data = _load_dashboard("consolidation/doctype/close_run/close_run_dashboard.py")
    assert data["internal_links"]["Pipeline Run"] == "pipeline_run"
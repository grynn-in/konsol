"""Whitelisted HTTP/bench entrypoints for external CLI and MCP clients."""
import json

import frappe

from konsol.config_service import (
    apply_config,
    apply_schema,
    diff_config,
    export_config,
    get_dimension,
    get_connector,
    get_fact_table,
    get_measure,
    get_schema_status,
    list_connectors,
    list_dimensions,
    list_erp_sources,
    list_fact_tables,
    list_measures,
    publish_dimension,
    publish_fact_table,
    publish_measure,
    unpublish_fact_table,
    upsert_connector,
    upsert_dimension,
    upsert_fact_table,
    upsert_measure,
)


def _status_filter(status):
    if not status:
        return None
    return {"status": status}


def _parse_spec(spec):
    if isinstance(spec, str):
        return json.loads(spec)
    return dict(spec or {})


@frappe.whitelist()
def list_dimensions_api(status=None):
    """List Dimension docs. Optional status: Draft, Published, Inactive."""
    return list_dimensions(_status_filter(status))


@frappe.whitelist()
def get_dimension_api(name):
    """Get a single Dimension doc by name."""
    return get_dimension(name)


@frappe.whitelist()
def upsert_dimension_api(spec, publish=False):
    """Create or update a Dimension doc. Pass spec as a JSON object."""
    return upsert_dimension(_parse_spec(spec), publish=frappe.utils.cint(publish))


@frappe.whitelist()
def publish_dimension_api(name):
    """Publish a Dimension doc and request a governed rebuild."""
    return publish_dimension(name)


@frappe.whitelist()
def list_measures_api(status=None):
    """List Measure docs. Optional status: Draft, Published, Inactive."""
    return list_measures(_status_filter(status))


@frappe.whitelist()
def get_measure_api(name):
    """Get a single Measure doc by name."""
    return get_measure(name)


@frappe.whitelist()
def upsert_measure_api(spec, publish=False):
    """Create or update a Measure doc. Pass spec as a JSON object."""
    return upsert_measure(_parse_spec(spec), publish=frappe.utils.cint(publish))


@frappe.whitelist()
def publish_measure_api(name):
    """Publish a Measure doc and request a governed rebuild."""
    return publish_measure(name)


@frappe.whitelist()
def apply_schema_api(run_dbt=False):
    """Apply schema from published config (dbt vars, ClickHouse DDL, budget fields)."""
    return apply_schema(run_dbt=frappe.utils.cint(run_dbt))


@frappe.whitelist()
def get_schema_status_api():
    """Return registry counts and pipeline build request status."""
    return get_schema_status()


@frappe.whitelist()
def list_fact_tables_api(status=None):
    """List Fact Table docs. Optional status: Draft, Published, or Inactive."""
    return list_fact_tables(_status_filter(status))


@frappe.whitelist()
def get_fact_table_api(name):
    """Get a single Fact Table doc by fact_name."""
    return get_fact_table(name)


@frappe.whitelist()
def upsert_fact_table_api(spec, publish=False):
    """Create or update a Fact Table doc. Pass spec as a JSON object."""
    return upsert_fact_table(_parse_spec(spec), publish=frappe.utils.cint(publish))


@frappe.whitelist()
def publish_fact_table_api(name):
    """Publish a Fact Table doc and request a governed rebuild."""
    return publish_fact_table(name)


@frappe.whitelist()
def unpublish_fact_table_api(name):
    """Unpublish a Fact Table doc and request a governed rebuild."""
    return unpublish_fact_table(name)


@frappe.whitelist()
def list_connectors_api(enabled=None):
    """List Connector docs. Optional enabled: 0 or 1."""
    filters = None
    if enabled is not None and str(enabled) != "":
        filters = {"enabled": frappe.utils.cint(enabled)}
    return list_connectors(filters)


@frappe.whitelist()
def get_connector_api(name):
    """Get a single Connector doc by name (CONN-.#####)."""
    return get_connector(name)


@frappe.whitelist()
def upsert_connector_api(spec):
    """Create or update a Connector doc. Pass spec as a JSON object."""
    return upsert_connector(_parse_spec(spec))


@frappe.whitelist()
def list_erp_sources_api():
    """Return enabled ERP source keys (dbt erp_sources)."""
    return list_erp_sources()


@frappe.whitelist()
def export_config_api(status=None):
    """Export dimensions, measures, and fact tables as a portable bundle."""
    return export_config(status=status)


@frappe.whitelist()
def apply_config_api(spec, publish=False):
    """Apply a config bundle (dimensions, measures, fact tables)."""
    return apply_config(_parse_spec(spec), publish=frappe.utils.cint(publish))


@frappe.whitelist()
def diff_config_api(spec, status=None):
    """Diff a config bundle against the live site."""
    return diff_config(_parse_spec(spec), status=status)
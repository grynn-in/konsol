"""Whitelisted HTTP/bench entrypoints for external CLI and MCP clients."""
import json

import frappe

from konsol.config_service import (
    apply_schema,
    get_dimension,
    get_measure,
    get_schema_status,
    list_dimensions,
    list_measures,
    publish_dimension,
    publish_measure,
    upsert_dimension,
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
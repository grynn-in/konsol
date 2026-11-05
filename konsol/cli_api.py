"""Whitelisted entry points that only this module offers (Deepak Pai, 5 Nov
2026: "slim cli_api to the five").

- apply_schema_api / get_schema_status_api: apply the published configuration
  to the warehouse (dbt vars, ClickHouse DDL, budget fields) and report on it.
  install.py points here when the dbt vars could not be regenerated.
- export_config_api / diff_config_api / apply_config_api: the whole
  configuration (dimensions, measures, datasets, connectors) as one bundle, to
  copy a site's setup to another site.

Everything else a client needs is an ordinary document operation or a
whitelisted document method (Dimension/Measure/Dataset publish and unpublish;
Connector test_extract_connection, test_writeback_connection,
provision_airbyte), which frappectl and the Desk call directly. The 23
wrappers that duplicated them were removed; konsol-cli, their only caller, is
frozen. Writes are POST-only: a GET request is rolled back at the end.
"""
import json

import frappe

from konsol.config_service import (
    apply_config,
    apply_schema,
    diff_config,
    export_config,
    get_schema_status,
)


def _parse_spec(spec):
    if isinstance(spec, str):
        return json.loads(spec)
    return dict(spec or {})


@frappe.whitelist(methods=["POST"])
def apply_schema_api(run_dbt=False):
    """Apply schema from published config (dbt vars, ClickHouse DDL, budget fields)."""
    return apply_schema(run_dbt=frappe.utils.cint(run_dbt))


@frappe.whitelist()
def get_schema_status_api():
    """Return registry counts and pipeline build request status."""
    return get_schema_status()


@frappe.whitelist()
def export_config_api(status=None):
    """Export dimensions, measures, datasets and connectors as a portable bundle."""
    return export_config(status=status)


@frappe.whitelist()
def diff_config_api(spec, status=None):
    """Diff a config bundle against the live site."""
    return diff_config(_parse_spec(spec), status=status)


@frappe.whitelist(methods=["POST"])
def apply_config_api(spec, publish=False, prune=False):
    """Apply a config bundle (dimensions, measures, datasets, connectors)."""
    return apply_config(
        _parse_spec(spec),
        publish=frappe.utils.cint(publish),
        prune=frappe.utils.cint(prune),
    )

"""Configuration service — stable API for CLI and MCP clients.

All EPM model reads and writes go through this module so clients never touch
dbt, ClickHouse, or SQL directly.
"""
import json

import frappe

_DIMENSION_FIELDS = [
    "name",
    "dimension_name",
    "source_column",
    "label",
    "cube_type",
    "in_budget",
    "allocation_role",
    "permission_doctype",
    "status",
]

_MEASURE_FIELDS = [
    "name",
    "measure_name",
    "expression",
    "label",
    "cube_type",
    "status",
]

_DIMENSION_WRITABLE_FIELDS = [
    "source_column",
    "label",
    "cube_type",
    "in_budget",
    "allocation_role",
    "permission_doctype",
]

_DIMENSION_CUBE_TYPES = {"string", "number"}
_MEASURE_CUBE_TYPES = {"sum", "count", "avg"}

_MEASURE_WRITABLE_FIELDS = [
    "expression",
    "label",
    "cube_type",
]

_FACT_LIST_FIELDS = [
    "name",
    "fact_name",
    "label",
    "source_type",
    "clickhouse_table",
    "dbt_model",
    "scenario_key",
    "has_scenario_id",
    "grain",
    "refresh_frequency",
    "generates_source",
    "status",
]

_FACT_DETAIL_FIELDS = _FACT_LIST_FIELDS + [
    "extra_columns",
    "reroute_measure",
    "reroute_table",
    "reroute_column",
]

_FACT_WRITABLE_FIELDS = [
    "label",
    "source_type",
    "clickhouse_table",
    "dbt_model",
    "scenario_key",
    "has_scenario_id",
    "grain",
    "refresh_frequency",
    "generates_source",
    "extra_columns",
    "reroute_measure",
    "reroute_table",
    "reroute_column",
]

_SOURCE_TYPES = {"ERP GL", "Budget", "Statistical", "Sub-ledger"}
_CONFIG_API_VERSION = "konsol/v1"


def _normalize_filters(filters):
    if not filters:
        return {}
    return dict(filters)


def _serialize_dimension(row):
    data = dict(row)
    data["in_budget"] = bool(data.get("in_budget"))
    return data


def _dimension_row(doc):
    return _serialize_dimension({field: getattr(doc, field, None) for field in _DIMENSION_FIELDS})


def _validate_dimension_spec(spec, *, require_core_fields):
    missing = [
        field
        for field in ("dimension_name", "source_column", "label")
        if require_core_fields and not spec.get(field)
    ]
    if missing:
        frappe.throw(
            f"Missing required dimension fields: {', '.join(missing)}",
            frappe.MandatoryError,
        )

    cube_type = spec.get("cube_type")
    if cube_type and cube_type not in _DIMENSION_CUBE_TYPES:
        frappe.throw(
            f"Invalid cube_type '{cube_type}'. Must be one of: {', '.join(sorted(_DIMENSION_CUBE_TYPES))}",
            frappe.ValidationError,
        )


def _measure_row(doc):
    return {field: getattr(doc, field, None) for field in _MEASURE_FIELDS}


def _validate_measure_spec(spec, *, require_core_fields):
    missing = [
        field
        for field in ("measure_name", "expression", "label")
        if require_core_fields and not spec.get(field)
    ]
    if missing:
        frappe.throw(
            f"Missing required measure fields: {', '.join(missing)}",
            frappe.MandatoryError,
        )

    cube_type = spec.get("cube_type")
    if cube_type and cube_type not in _MEASURE_CUBE_TYPES:
        frappe.throw(
            f"Invalid cube_type '{cube_type}'. Must be one of: {', '.join(sorted(_MEASURE_CUBE_TYPES))}",
            frappe.ValidationError,
        )


def list_dimensions(filters=None):
    """Return all Dimension docs matching optional filters."""
    rows = frappe.get_all(
        "Dimension",
        filters=_normalize_filters(filters),
        fields=_DIMENSION_FIELDS,
        order_by="dimension_name asc",
        limit_page_length=0,
    )
    return [_serialize_dimension(row) for row in rows]


def get_dimension(name):
    """Return a single Dimension doc by name."""
    if not frappe.db.exists("Dimension", name):
        frappe.throw(f"Dimension '{name}' not found", frappe.DoesNotExistError)
    return _dimension_row(frappe.get_doc("Dimension", name))


def upsert_dimension(spec, publish=False):
    """Create or update a Dimension doc. Saves as Draft unless publish=True."""
    spec = dict(spec or {})
    name = spec.get("dimension_name")
    if not name:
        frappe.throw("dimension_name is required", frappe.MandatoryError)

    created = not frappe.db.exists("Dimension", name)
    _validate_dimension_spec(spec, require_core_fields=created)

    if created:
        doc = frappe.new_doc("Dimension")
        doc.dimension_name = name
        doc.status = spec.get("status", "Draft")
        for field in _DIMENSION_WRITABLE_FIELDS:
            if field in spec:
                setattr(doc, field, spec[field])
        if "cube_type" not in spec:
            doc.cube_type = "string"
    else:
        doc = frappe.get_doc("Dimension", name)
        for field in _DIMENSION_WRITABLE_FIELDS:
            if field in spec:
                setattr(doc, field, spec[field])
        if "status" in spec and not publish:
            doc.status = spec["status"]

    doc.save()
    frappe.db.commit()

    if publish:
        doc.publish()

    doc.reload()

    return {
        "created": created,
        "published": bool(publish),
        "dimension": _dimension_row(doc),
    }


def publish_dimension(name):
    """Publish a Dimension: apply schema and request a governed rebuild."""
    if not frappe.db.exists("Dimension", name):
        frappe.throw(f"Dimension '{name}' not found", frappe.DoesNotExistError)

    doc = frappe.get_doc("Dimension", name)
    doc.publish()
    doc.reload()

    return {
        "published": True,
        "dimension": _dimension_row(doc),
    }


def unpublish_dimension(name):
    """Unpublish a Dimension: mark Inactive and request a governed rebuild."""
    if not frappe.db.exists("Dimension", name):
        frappe.throw(f"Dimension '{name}' not found", frappe.DoesNotExistError)

    doc = frappe.get_doc("Dimension", name)
    doc.unpublish()
    doc.reload()

    return {
        "unpublished": True,
        "dimension": _dimension_row(doc),
    }


def list_measures(filters=None):
    """Return all Measure docs matching optional filters."""
    return frappe.get_all(
        "Measure",
        filters=_normalize_filters(filters),
        fields=_MEASURE_FIELDS,
        order_by="measure_name asc",
        limit_page_length=0,
    )


def get_measure(name):
    """Return a single Measure doc by name."""
    if not frappe.db.exists("Measure", name):
        frappe.throw(f"Measure '{name}' not found", frappe.DoesNotExistError)
    return _measure_row(frappe.get_doc("Measure", name))


def upsert_measure(spec, publish=False):
    """Create or update a Measure doc. Saves as Draft unless publish=True."""
    spec = dict(spec or {})
    name = spec.get("measure_name")
    if not name:
        frappe.throw("measure_name is required", frappe.MandatoryError)

    created = not frappe.db.exists("Measure", name)
    _validate_measure_spec(spec, require_core_fields=created)

    if created:
        doc = frappe.new_doc("Measure")
        doc.measure_name = name
        doc.status = spec.get("status", "Draft")
        for field in _MEASURE_WRITABLE_FIELDS:
            if field in spec:
                setattr(doc, field, spec[field])
        if "cube_type" not in spec:
            doc.cube_type = "sum"
    else:
        doc = frappe.get_doc("Measure", name)
        for field in _MEASURE_WRITABLE_FIELDS:
            if field in spec:
                setattr(doc, field, spec[field])
        if "status" in spec and not publish:
            doc.status = spec["status"]

    doc.save()
    frappe.db.commit()

    if publish:
        doc.publish()

    doc.reload()

    return {
        "created": created,
        "published": bool(publish),
        "measure": _measure_row(doc),
    }


_PENDING_BUILD_STATES = ["Draft", "Pending Review", "Approved", "Running"]
_TERMINAL_BUILD_STATES = ["Completed", "Failed", "Cancelled"]
_BUILD_REQUEST_FIELDS = [
    "name",
    "build_scope",
    "workflow_state",
    "risk_level",
    "trigger_source",
    "trigger_doctype",
    "trigger_docname",
    "requested_by",
    "modified",
]


def _status_counts(doctype):
    counts = {"Draft": 0, "Published": 0, "Inactive": 0}
    for row in frappe.get_all(doctype, fields=["status"], limit_page_length=0):
        status = row.get("status") if isinstance(row, dict) else row.status
        status = status or "Draft"
        counts[status] = counts.get(status, 0) + 1
    return counts


def apply_schema(run_dbt=False):
    """Apply schema changes from all published config doctypes."""
    from konsol.schema_apply import apply_schema as _apply_schema

    return _apply_schema(run_dbt=run_dbt)


def get_schema_status():
    """Summarize config registry counts and pipeline build request state."""
    registry = {
        "dimensions": _status_counts("Dimension"),
        "measures": _status_counts("Measure"),
    }
    if frappe.db.table_exists("Fact Table"):
        registry["fact_tables"] = _status_counts("Fact Table")

    pending_builds = frappe.get_all(
        "Pipeline Build Request",
        filters={"workflow_state": ["in", _PENDING_BUILD_STATES]},
        fields=_BUILD_REQUEST_FIELDS,
        order_by="modified desc",
        limit_page_length=0,
    )
    recent_builds = frappe.get_all(
        "Pipeline Build Request",
        filters={"workflow_state": ["in", _TERMINAL_BUILD_STATES]},
        fields=_BUILD_REQUEST_FIELDS,
        order_by="modified desc",
        limit=5,
    )

    return {
        "registry": registry,
        "pending_builds": pending_builds,
        "recent_builds": recent_builds,
    }


def publish_measure(name):
    """Publish a Measure: apply schema and request a governed rebuild."""
    if not frappe.db.exists("Measure", name):
        frappe.throw(f"Measure '{name}' not found", frappe.DoesNotExistError)

    doc = frappe.get_doc("Measure", name)
    doc.publish()
    doc.reload()

    return {
        "published": True,
        "measure": _measure_row(doc),
    }


def unpublish_measure(name):
    """Unpublish a Measure: mark Inactive and request a governed rebuild."""
    if not frappe.db.exists("Measure", name):
        frappe.throw(f"Measure '{name}' not found", frappe.DoesNotExistError)

    doc = frappe.get_doc("Measure", name)
    doc.unpublish()
    doc.reload()

    return {
        "unpublished": True,
        "measure": _measure_row(doc),
    }


def _fact_measures_list(doc):
    if doc.fact_measures:
        return [
            {"measure": row.measure, "required": bool(row.required)}
            for row in doc.fact_measures
        ]
    parsed = json.loads(doc.measures or "[]")
    return [
        item if isinstance(item, dict) else {"measure": item, "required": False}
        for item in parsed
    ]


def _fact_dimensions_list(doc):
    if doc.fact_dimensions:
        return [
            {"dimension": row.dimension, "required": bool(row.required)}
            for row in doc.fact_dimensions
        ]
    parsed = json.loads(doc.dimensions or "[]")
    return [
        item if isinstance(item, dict) else {"dimension": item, "required": False}
        for item in parsed
    ]


def _serialize_fact_row(doc, *, include_detail=False):
    fields = _FACT_DETAIL_FIELDS if include_detail else _FACT_LIST_FIELDS
    data = {field: getattr(doc, field, None) for field in fields}
    data["has_scenario_id"] = bool(data.get("has_scenario_id"))
    data["generates_source"] = bool(data.get("generates_source"))
    data["measures"] = _fact_measures_list(doc)
    data["dimensions"] = _fact_dimensions_list(doc)
    return data


def _validate_fact_spec(spec, *, require_core_fields):
    missing = [
        field
        for field in ("fact_name", "label", "source_type", "clickhouse_table", "scenario_key")
        if require_core_fields and not spec.get(field)
    ]
    if missing:
        frappe.throw(
            f"Missing required fact table fields: {', '.join(missing)}",
            frappe.MandatoryError,
        )

    source_type = spec.get("source_type")
    if source_type and source_type not in _SOURCE_TYPES:
        frappe.throw(
            f"Invalid source_type '{source_type}'. Must be one of: {', '.join(sorted(_SOURCE_TYPES))}",
            frappe.ValidationError,
        )


def _set_fact_child_rows(doc, spec):
    if "measures" in spec:
        doc.fact_measures = []
        for item in spec.get("measures") or []:
            if isinstance(item, str):
                doc.append("fact_measures", {"measure": item})
            else:
                doc.append(
                    "fact_measures",
                    {
                        "measure": item["measure"],
                        "required": int(bool(item.get("required"))),
                    },
                )

    if "dimensions" in spec:
        doc.fact_dimensions = []
        for item in spec.get("dimensions") or []:
            if isinstance(item, str):
                doc.append("fact_dimensions", {"dimension": item})
            else:
                doc.append(
                    "fact_dimensions",
                    {
                        "dimension": item["dimension"],
                        "required": int(bool(item.get("required"))),
                    },
                )


def list_fact_tables(filters=None):
    """Return all Fact Table docs matching optional filters."""
    rows = frappe.get_all(
        "Fact Table",
        filters=_normalize_filters(filters),
        fields=_FACT_LIST_FIELDS,
        order_by="fact_name asc",
        limit_page_length=0,
    )
    result = []
    for row in rows:
        doc = frappe.get_doc("Fact Table", row["name"])
        result.append(_serialize_fact_row(doc))
    return result


def get_fact_table(name):
    """Return a single Fact Table doc by fact_name."""
    if not frappe.db.exists("Fact Table", name):
        frappe.throw(f"Fact Table '{name}' not found", frappe.DoesNotExistError)
    return _serialize_fact_row(frappe.get_doc("Fact Table", name), include_detail=True)


def upsert_fact_table(spec, publish=False):
    """Create or update a Fact Table doc. Saves as Draft unless publish=True."""
    spec = dict(spec or {})
    name = spec.get("fact_name")
    if not name:
        frappe.throw("fact_name is required", frappe.MandatoryError)

    created = not frappe.db.exists("Fact Table", name)
    _validate_fact_spec(spec, require_core_fields=created)

    if created:
        doc = frappe.new_doc("Fact Table")
        doc.fact_name = name
        doc.status = spec.get("status", "Draft")
        for field in _FACT_WRITABLE_FIELDS:
            if field in spec:
                setattr(doc, field, spec[field])
        _set_fact_child_rows(doc, spec)
    else:
        doc = frappe.get_doc("Fact Table", name)
        for field in _FACT_WRITABLE_FIELDS:
            if field in spec:
                setattr(doc, field, spec[field])
        if "status" in spec and not publish:
            doc.status = spec["status"]
        _set_fact_child_rows(doc, spec)

    doc.save()
    frappe.db.commit()

    if publish:
        doc.publish()

    doc.reload()

    return {
        "created": created,
        "published": bool(publish),
        "fact_table": _serialize_fact_row(doc, include_detail=True),
    }


def publish_fact_table(name):
    """Publish a Fact Table: apply schema and request a governed rebuild."""
    if not frappe.db.exists("Fact Table", name):
        frappe.throw(f"Fact Table '{name}' not found", frappe.DoesNotExistError)

    doc = frappe.get_doc("Fact Table", name)
    doc.publish()
    doc.reload()

    return {
        "published": True,
        "fact_table": _serialize_fact_row(doc, include_detail=True),
    }


def unpublish_fact_table(name):
    """Unpublish a Fact Table: mark Inactive and request a governed rebuild."""
    if not frappe.db.exists("Fact Table", name):
        frappe.throw(f"Fact Table '{name}' not found", frappe.DoesNotExistError)

    doc = frappe.get_doc("Fact Table", name)
    doc.unpublish()
    doc.reload()

    return {
        "unpublished": True,
        "fact_table": _serialize_fact_row(doc, include_detail=True),
    }


_CONNECTOR_LIST_FIELDS = [
    "name",
    "connector_name",
    "erp_type",
    "enabled",
    "airbyte_connection_id",
    "airbyte_source",
    "dbt_adapter_prefix",
    "last_sync_at",
    "last_sync_status",
    "last_sync_rows",
    "sync_frequency_minutes",
]

_CONNECTOR_WRITABLE_FIELDS = [
    "connector_name",
    "erp_type",
    "enabled",
    "airbyte_connection_id",
    "sync_frequency_minutes",
]

_CONNECTOR_EXPORT_FIELDS = list(_CONNECTOR_WRITABLE_FIELDS)

_ERP_TYPES = {"d365_fo", "d365_bc", "sap_s4", "sap_ecc", "sap_b1", "erpnext"}


def _connector_row(doc, *, include_children=False):
    data = {field: getattr(doc, field, None) for field in _CONNECTOR_LIST_FIELDS}
    data["enabled"] = bool(data.get("enabled"))
    if include_children:
        data["legal_entities"] = [
            {"entity_id": row.entity_id, "entity_name": row.entity_name}
            for row in (doc.legal_entities or [])
        ]
        data["dimension_mappings"] = [
            {"dimension": row.dimension, "source_column": row.source_column}
            for row in (doc.dimension_mappings or [])
        ]
    return data


def _validate_connector_spec(spec, *, require_core_fields):
    missing = [
        field
        for field in ("connector_name", "erp_type")
        if require_core_fields and not spec.get(field)
    ]
    if missing:
        frappe.throw(
            f"Missing required connector fields: {', '.join(missing)}",
            frappe.MandatoryError,
        )

    erp_type = spec.get("erp_type")
    if erp_type and erp_type not in _ERP_TYPES:
        frappe.throw(
            f"Invalid erp_type '{erp_type}'. Must be one of: {', '.join(sorted(_ERP_TYPES))}",
            frappe.ValidationError,
        )


def _resolve_connector_name(spec):
    if spec.get("name") and frappe.db.exists("Connector", spec["name"]):
        return spec["name"]
    connector_name = spec.get("connector_name")
    if connector_name:
        matches = frappe.get_all(
            "Connector",
            filters={"connector_name": connector_name},
            pluck="name",
            limit=1,
        )
        if matches:
            return matches[0]
    return None


def _set_connector_child_rows(doc, spec):
    if "legal_entities" in spec:
        doc.legal_entities = []
        for item in spec.get("legal_entities") or []:
            if isinstance(item, str):
                doc.append("legal_entities", {"entity_id": item})
            else:
                doc.append(
                    "legal_entities",
                    {
                        "entity_id": item["entity_id"],
                        "entity_name": item.get("entity_name"),
                    },
                )

    if "dimension_mappings" in spec:
        doc.dimension_mappings = []
        for item in spec.get("dimension_mappings") or []:
            doc.append(
                "dimension_mappings",
                {
                    "dimension": item["dimension"],
                    "source_column": item["source_column"],
                },
            )


def list_connectors(filters=None):
    """Return all Connector docs matching optional filters."""
    if not frappe.db.table_exists("Connector"):
        return []
    rows = frappe.get_all(
        "Connector",
        filters=_normalize_filters(filters),
        fields=_CONNECTOR_LIST_FIELDS,
        order_by="connector_name asc",
        limit_page_length=0,
    )
    return [_connector_row(frappe.get_doc("Connector", row["name"])) for row in rows]


def get_connector(name):
    """Return a single Connector doc by name (CONN-.#####)."""
    if not frappe.db.exists("Connector", name):
        frappe.throw(f"Connector '{name}' not found", frappe.DoesNotExistError)
    return _connector_row(frappe.get_doc("Connector", name), include_children=True)


def _resolve_connector_docname(name):
    if frappe.db.exists("Connector", name):
        return name
    matches = frappe.get_all(
        "Connector",
        filters={"connector_name": name},
        pluck="name",
        limit=1,
    )
    if matches:
        return matches[0]
    frappe.throw(f"Connector '{name}' not found", frappe.DoesNotExistError)


def delete_connector(name):
    """Delete a Connector by ID (CONN-...) or connector_name."""
    if not frappe.db.table_exists("Connector"):
        frappe.throw("Connector doctype is not installed", frappe.DoesNotExistError)

    docname = _resolve_connector_docname(name)
    connector_name = frappe.db.get_value("Connector", docname, "connector_name")
    frappe.delete_doc("Connector", docname)
    frappe.db.commit()

    return {
        "deleted": True,
        "name": docname,
        "connector_name": connector_name,
    }


def upsert_connector(spec):
    """Create or update a Connector doc. Regenerates erp_sources on save."""
    if not frappe.db.table_exists("Connector"):
        frappe.throw("Connector doctype is not installed", frappe.DoesNotExistError)

    spec = dict(spec or {})
    existing = _resolve_connector_name(spec)
    created = existing is None
    _validate_connector_spec(spec, require_core_fields=created)

    if created:
        doc = frappe.new_doc("Connector")
        for field in _CONNECTOR_WRITABLE_FIELDS:
            if field in spec:
                setattr(doc, field, spec[field])
        _set_connector_child_rows(doc, spec)
    else:
        doc = frappe.get_doc("Connector", existing)
        for field in _CONNECTOR_WRITABLE_FIELDS:
            if field in spec:
                setattr(doc, field, spec[field])
        _set_connector_child_rows(doc, spec)

    doc.save()
    frappe.db.commit()
    doc.reload()

    return {
        "created": created,
        "connector": _connector_row(doc, include_children=True),
    }


def list_erp_sources():
    """Return enabled ERP source keys that drive dbt erp_sources."""
    from konsol.dbt_config import _build_erp_sources_vars

    return {"erp_sources": _build_erp_sources_vars()}


def _config_entity_key(doctype, row):
    if doctype == "Dimension":
        return row.get("dimension_name")
    if doctype == "Measure":
        return row.get("measure_name")
    if doctype == "Fact Table":
        return row.get("fact_name")
    if doctype == "Connector":
        return row.get("connector_name")
    return row.get("name")


def _normalize_config_bundle(spec):
    bundle = dict(spec or {})
    if bundle.get("api_version") and bundle["api_version"] != _CONFIG_API_VERSION:
        frappe.throw(
            f"Unsupported api_version '{bundle['api_version']}'. Expected {_CONFIG_API_VERSION}.",
            frappe.ValidationError,
        )
    return bundle


def _export_rows(doctype, list_fn, status=None):
    filters = {"status": status} if status else None
    rows = list_fn(filters)
    for row in rows:
        row.pop("name", None)
    return rows


def _export_connector_rows():
    if not frappe.db.table_exists("Connector"):
        return []

    exported = []
    for summary in list_connectors():
        full = get_connector(summary["name"])
        row = {field: full[field] for field in _CONNECTOR_EXPORT_FIELDS}
        row["enabled"] = bool(row.get("enabled"))
        if full.get("legal_entities"):
            row["legal_entities"] = [
                {
                    "entity_id": entity["entity_id"],
                    "entity_name": entity.get("entity_name"),
                }
                for entity in full["legal_entities"]
            ]
        if full.get("dimension_mappings"):
            row["dimension_mappings"] = list(full["dimension_mappings"])
        exported.append(row)
    return exported


def export_config(status=None):
    """Export dimensions, measures, fact tables, and connectors as a portable bundle."""
    return {
        "api_version": _CONFIG_API_VERSION,
        "dimensions": _export_rows("Dimension", list_dimensions, status),
        "measures": _export_rows("Measure", list_measures, status),
        "fact_tables": _export_rows("Fact Table", list_fact_tables, status),
        "connectors": _export_connector_rows(),
    }


def _diff_section(doctype, desired_rows, live_rows):
    desired = {
        _config_entity_key(doctype, row): row
        for row in desired_rows
        if _config_entity_key(doctype, row)
    }
    live = {
        _config_entity_key(doctype, row): row
        for row in live_rows
        if _config_entity_key(doctype, row)
    }

    added = []
    modified = []
    unchanged = []
    only_on_site = []

    for key, row in desired.items():
        if key not in live:
            added.append({"key": key, "desired": row})
            continue
        if row != live[key]:
            modified.append({"key": key, "desired": row, "live": live[key]})
        else:
            unchanged.append(key)

    for key, row in live.items():
        if key not in desired:
            only_on_site.append({"key": key, "live": row})

    return {
        "added": added,
        "modified": modified,
        "unchanged": unchanged,
        "only_on_site": only_on_site,
    }


def diff_config(spec, status=None):
    """Compare a config bundle against the live site."""
    bundle = _normalize_config_bundle(spec)
    live = export_config(status=status)
    return {
        "dimensions": _diff_section(
            "Dimension", bundle.get("dimensions", []), live["dimensions"]
        ),
        "measures": _diff_section("Measure", bundle.get("measures", []), live["measures"]),
        "fact_tables": _diff_section(
            "Fact Table", bundle.get("fact_tables", []), live["fact_tables"]
        ),
        "connectors": _diff_section(
            "Connector", bundle.get("connectors", []), live["connectors"]
        ),
    }


def _remove_config_entity(doctype, key):
    if doctype == "Connector":
        return delete_connector(key)

    if doctype == "Dimension":
        if not frappe.db.exists("Dimension", key):
            return None
        doc = frappe.get_doc("Dimension", key)
        if doc.status == "Published":
            doc.unpublish()
            action = "unpublished"
        else:
            frappe.delete_doc("Dimension", key)
            action = "deleted"
        frappe.db.commit()
        return {"key": key, "action": action}

    if doctype == "Measure":
        if not frappe.db.exists("Measure", key):
            return None
        doc = frappe.get_doc("Measure", key)
        if doc.status == "Published":
            doc.unpublish()
            action = "unpublished"
        elif doc.status == "Inactive":
            frappe.delete_doc("Measure", key)
            action = "deleted"
        else:
            frappe.delete_doc("Measure", key)
            action = "deleted"
        frappe.db.commit()
        return {"key": key, "action": action}

    if doctype == "Fact Table":
        if not frappe.db.exists("Fact Table", key):
            return None
        doc = frappe.get_doc("Fact Table", key)
        if doc.status == "Published":
            doc.unpublish()
            action = "unpublished"
        else:
            frappe.delete_doc("Fact Table", key)
            action = "deleted"
        frappe.db.commit()
        return {"key": key, "action": action}

    return None


def _prune_config_diff(diff, bundle):
    """Remove only_on_site entities for sections declared in the bundle."""
    pruned = {
        "dimensions": [],
        "measures": [],
        "fact_tables": [],
        "connectors": [],
    }
    sections = (
        ("dimensions", "Dimension"),
        ("measures", "Measure"),
        ("fact_tables", "Fact Table"),
        ("connectors", "Connector"),
    )
    for section_key, doctype in sections:
        if section_key not in bundle:
            continue
        for item in diff.get(section_key, {}).get("only_on_site", []):
            result = _remove_config_entity(doctype, item["key"])
            if result:
                pruned[section_key].append(result)
    return pruned


def apply_config(spec, publish=False, prune=False):
    """Apply a config bundle via upsert for each entity."""
    bundle = _normalize_config_bundle(spec)
    summary = {
        "dimensions": [],
        "measures": [],
        "fact_tables": [],
        "connectors": [],
    }

    for row in bundle.get("dimensions", []):
        summary["dimensions"].append(upsert_dimension(row, publish=publish))
    for row in bundle.get("measures", []):
        summary["measures"].append(upsert_measure(row, publish=publish))
    for row in bundle.get("fact_tables", []):
        summary["fact_tables"].append(upsert_fact_table(row, publish=publish))
    for row in bundle.get("connectors", []):
        summary["connectors"].append(upsert_connector(row))

    if prune:
        summary["pruned"] = _prune_config_diff(diff_config(bundle), bundle)

    return summary
"""Configuration service — stable API for CLI and MCP clients.

All EPM model reads and writes go through this module so clients never touch
dbt, ClickHouse, or SQL directly.
"""
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
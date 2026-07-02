"""Schema Lifecycle — shared publish/unpublish helpers for Dimension and Measure.

Extracted to avoid code duplication between the two doctypes.
"""
import frappe

_ALLOWED_ROLES = {"EPM Admin", "System Manager", "Administrator"}


def check_epm_admin():
    """Guard: require EPM Admin, System Manager, or Administrator role."""
    if not _ALLOWED_ROLES.intersection(set(frappe.get_roles())):
        frappe.throw(
            "You need the 'EPM Admin' role to publish or unpublish.",
            frappe.PermissionError,
        )


# Config-doctype publishes (Dimension/Measure/Dataset) are schema-level
# changes that can ripple through every dbt model, so they request a full-scope
# rebuild. Routing through Build Approval (instead of a direct dbt build)
# applies Build Governance: preflight (won't wipe gold when epm_raw is empty),
# approval for high-risk scopes, an audit trail, and debounce.
_PUBLISH_BUILD_SCOPE = "full"
_PENDING_STATES = ["Draft", "Pending Review", "Approved", "Running"]


def apply_and_rebuild(doc, action):
    """Apply schema (DDL/vars), then request a governed dbt rebuild.

    Creates a full-scope Build Approval rather than firing a direct
    `dbt build` — see module note. Returns the PBR name (or the existing one if
    a build for this scope is already pending).
    """
    from konsol.schema_apply import apply_schema
    apply_schema()
    return _request_governed_build(doc, action)


def request_governed_rebuild(doc, action, scope=_PUBLISH_BUILD_SCOPE):
    """Request a governed dbt rebuild for an input-only change (no DDL step).

    For changes that only affect dbt inputs (e.g. the dimension_mappings seed),
    not the ClickHouse schema/vars — so there is no DDL/schema step to run, just
    a governed build (which runs `dbt seed` + models). Same PBR machinery as the
    full publish path (preflight + approval + audit + debounce).
    """
    return _request_governed_build(doc, action, scope)


def _request_governed_build(doc, action, scope=_PUBLISH_BUILD_SCOPE):
    """Create a (debounced) Build Approval for `scope`.

    Debounce: if a non-terminal build for the same scope already exists, reuse
    it so publishing several config docs in a row coalesces into one rebuild.
    The PBR's own workflow handles risk → approval → preflight → governed build.
    """
    existing = frappe.get_all(
        "Build Approval",
        filters={"build_scope": scope, "workflow_state": ["in", _PENDING_STATES]},
        limit=1,
    )
    if existing:
        frappe.msgprint(
            f"A '{scope}' build is already pending ({existing[0].name}); "
            f"schema applied — no duplicate build requested."
        )
        return existing[0].name

    pbr = frappe.new_doc("Build Approval")
    pbr.build_scope = scope
    pbr.trigger_source = "auto"
    pbr.trigger_doctype = doc.doctype
    pbr.trigger_docname = doc.name
    pbr.requested_by = frappe.session.user
    pbr.insert(ignore_permissions=True)
    frappe.db.commit()

    frappe.msgprint(
        f"Schema applied. Build request {pbr.name} created (scope={scope}). "
        f"High-risk builds require EPM Admin approval before running."
    )
    return pbr.name

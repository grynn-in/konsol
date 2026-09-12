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

    # The DDL first, then the build request, so the request's row locks are
    # held only briefly at the end of the transaction and never across
    # ClickHouse ALTERs (#133 re-review). apply_schema() collects its step
    # errors instead of raising, so a failed DDL does not stop the request.
    # The trade-off: if the request then fails (a lock-wait timeout, say), the
    # ClickHouse DDL stays applied; it only adds tables and columns, so
    # re-publishing recovers. Nor is this atomic for a budget dimension:
    # apply_schema()'s Budget Line Custom Field sync commits through
    # frappe.db.updatedb, so the publish is already committed here (konsol#135).
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
    # Serialise every build request (konsol.build_lock): the debounce below is
    # check-then-insert, and two requests at once both found nothing pending.
    from konsol.build_lock import flag_running_build, lock_build_requests

    lock_build_requests()
    # A LOCKING read. Under REPEATABLE READ a plain read reuses the snapshot from
    # the transaction's first read, taken before the build lock above: a
    # request that waited on the lock would not see the approval the holder had
    # just committed, and would insert a duplicate (#133 review). FOR UPDATE
    # reads the latest committed rows.
    existing = frappe.db.sql(
        """SELECT name, workflow_state FROM `tabBuild Approval`
           WHERE build_scope = %(scope)s AND workflow_state IN %(states)s
           LIMIT 1 FOR UPDATE""",
        {"scope": scope, "states": tuple(_PENDING_STATES)},
        as_dict=True,
    )
    if existing:
        # A Running build may already have read its inputs: flag it for one
        # more build when it finishes (#129). Commits with the caller.
        flag_running_build(existing[0])
        frappe.msgprint(
            f"A '{scope}' build is already pending ({existing[0].name}); "
            f"no duplicate build requested."
        )
        return existing[0].name

    pbr = frappe.new_doc("Build Approval")
    pbr.build_scope = scope
    pbr.trigger_source = "auto"
    pbr.trigger_doctype = doc.doctype
    pbr.trigger_docname = doc.name
    pbr.requested_by = frappe.session.user
    # No commit (konsol#130). The approval commits or rolls back WITH the
    # caller's transaction. Committing here made AllocationRun.before_submit,
    # every publish, and GovernedReferenceDocument.after_delete non-atomic: a
    # submit that failed after this point left an orphaned approval for a run
    # that never existed. The commit used to be needed so the build job could
    # see the row; since #128, BuildApproval._enqueue_build enqueues only
    # after the commit, so it can.
    pbr.insert(ignore_permissions=True)

    frappe.msgprint(
        f"Build request {pbr.name} created (scope={scope}). "
        f"High-risk builds require EPM Admin approval before running."
    )
    return pbr.name

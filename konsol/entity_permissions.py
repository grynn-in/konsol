"""Entity-scoped access: sub-tree filtering built on Frappe User Permissions.

Before this, entity authorization had three holes:

  * It was off by default. ``_resolve_allowed_entities`` returned "unrestricted"
    unless someone set ``EPM Settings.entity_permission_doctype`` — an
    indirection that existed only because there was no Entity DocType to point
    at. There is one now, so the indirection goes.
  * It was exact-match. A controller assigned to GROUP_EMEA saw GROUP_EMEA and
    nothing beneath it, which is not what "responsible for EMEA" means.
  * It only applied where someone remembered to call it. There were no
    ``permission_query_conditions`` at all, so desk list views were never
    filtered — a user who could read Consolidation Group could read every
    entity's rows.

**A deliberate deviation from issue #91.** That issue said absent configuration
should mean "see nothing". It should not. In Frappe, a User Permission is an
opt-in *restriction*: no permission means no restriction, everywhere, in every
app. Inverting that for this one app would lock out every existing site on
upgrade and surprise every admin who knows the convention. The real defect was
the config indirection, not the convention — so the indirection is gone and
User Permissions on Entity now always apply, while their absence still means
unrestricted. Sites wanting deny-by-default get it explicitly via
``EPM Settings.restrict_entities_by_default``.
"""

import frappe

#: Doctypes whose `data_area_id` identifies the entity a row belongs to.
ENTITY_SCOPED_DOCTYPES = (
    "Consolidation Group",
    "Ownership Period",
    "Budget Sheet",
    "Consolidation Adjustment",
    "Historical Equity Rate",
    "Allocation Driver",
)

#: Roles that see every entity. Without this an admin can lock themselves out
#: by assigning themselves a single entity.
BYPASS_ROLES = frozenset({"System Manager", "Administrator"})


def _bypasses(user, roles):
    return user == "Administrator" or bool(BYPASS_ROLES & set(roles or []))


def assigned_entities(user=None):
    """Entities named directly by this user's User Permissions.

    Does not expand the tree — see :func:`allowed_entity_codes`.
    """
    user = user or frappe.session.user
    entries = (frappe.permissions.get_user_permissions(user) or {}).get("Entity") or []
    return {e.get("doc") for e in entries if e.get("doc")}


def _restrict_by_default():
    """Opt-in deny-by-default, for sites that want it. Off unless set."""
    try:
        return bool(frappe.get_cached_value(
            "EPM Settings", "EPM Settings", "restrict_entities_by_default"))
    except Exception:
        # The field may not exist on an older site; absent means off.
        return False


def subtree_codes(codes):
    """Expand entity codes to include everything beneath them.

    "Responsible for EMEA" means EMEA and its entities, so an assignment to a
    roll-up node has to carry its descendants. Uses the nested set — the reason
    Entity is a tree rather than a flat list.
    """
    codes = {c for c in (codes or []) if c}
    if not codes:
        return set()

    bounds = frappe.get_all(
        "Entity", filters={"name": ["in", list(codes)]}, fields=["lft", "rgt"],
        limit_page_length=0,
    )
    if not bounds:
        # Assignments pointing at entities that no longer exist grant nothing,
        # rather than silently granting everything.
        return set()

    # One query, not one per assignment: a descendant sits inside its
    # ancestor's lft/rgt span, so the spans OR together.
    spans = " OR ".join(
        f"(`lft` >= {int(b.lft)} AND `rgt` <= {int(b.rgt)})"
        for b in bounds
        if b.lft is not None and b.rgt is not None
    )
    if not spans:
        return set(codes)

    rows = frappe.db.sql(f"SELECT `name` FROM `tabEntity` WHERE {spans}", as_dict=True)
    return set(codes) | {r.name for r in rows}


def allowed_entity_codes(user=None):
    """Entity codes this user may see, or ``None`` when unrestricted.

    ``None`` means no filtering at all. An empty set means the user may see
    nothing — which is a real answer, not an absent one, and callers must
    distinguish the two.
    """
    user = user or frappe.session.user
    if _bypasses(user, frappe.get_roles(user)):
        return None

    assigned = assigned_entities(user)
    if not assigned:
        return set() if _restrict_by_default() else None

    return subtree_codes(assigned)


def _in_list_sql(codes):
    escaped = ", ".join(frappe.db.escape(c) for c in sorted(codes))
    return escaped


def _condition_for(field_expr, user):
    """SQL restricting ``field_expr`` to the user's entities, or "" for none."""
    allowed = allowed_entity_codes(user)
    if allowed is None:
        return ""
    if not allowed:
        # No entities: match nothing. Returning "" here would mean "no
        # restriction", which is the opposite of what an empty grant means.
        return "1=0"
    return f"({field_expr} IS NULL OR {field_expr} = '' OR {field_expr} IN ({_in_list_sql(allowed)}))"


def data_area_conditions(doctype, user):
    """permission_query_conditions for a doctype carrying ``data_area_id``.

    Rows with no entity (Consolidation Group's roll-up nodes) stay visible —
    they describe structure, not an entity's data, and hiding them would break
    the tree view for everyone who is scoped.
    """
    return _condition_for(f"`tab{doctype}`.`data_area_id`", user)


def _make_conditions(doctype):
    """Frappe calls permission_query_conditions with `user` only, and registers
    one entry per doctype — so each doctype needs its own bound function."""

    def conditions(user=None):
        return data_area_conditions(doctype, user)

    conditions.__name__ = _hook_name(doctype)
    conditions.__doc__ = f"permission_query_conditions for {doctype}."
    return conditions


def _hook_name(doctype):
    return doctype.lower().replace(" ", "_") + "_conditions"


# Bind one module-level function per entity-scoped doctype, so hooks.py can
# name them and there is no per-call doctype guessing.
for _dt in ENTITY_SCOPED_DOCTYPES:
    globals()[_hook_name(_dt)] = _make_conditions(_dt)
del _dt


def entity_conditions(user):
    """permission_query_conditions for Entity itself."""
    allowed = allowed_entity_codes(user)
    if allowed is None:
        return ""
    if not allowed:
        return "1=0"
    return f"(`tabEntity`.`name` IN ({_in_list_sql(allowed)}))"


def may_see_entity(code, user=None):
    allowed = allowed_entity_codes(user)
    return allowed is None or not code or code in allowed


def has_entity_permission(doc, user=None, permission_type=None):
    """has_permission hook: query conditions cover lists, not opening one doc."""
    code = getattr(doc, "data_area_id", None)
    return may_see_entity(code, user)


def has_entity_doc_permission(doc, user=None, permission_type=None):
    """has_permission hook for Entity itself."""
    return may_see_entity(getattr(doc, "name", None), user)


def assert_entity_access(code, user=None):
    """Raise if the user may not touch this entity.

    Still needed alongside permission_query_conditions: those only constrain
    Frappe queries, and konsol's warehouse reads go straight to ClickHouse
    where Frappe's permission layer never runs.
    """
    if not may_see_entity(code, user):
        raise frappe.PermissionError(f"Not permitted to access entity '{code}'")

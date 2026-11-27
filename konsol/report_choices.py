"""The entity and group lists the Excel add-in's report pickers show.

The same lists validate the parameters a report is submitted with
(:func:`is_allowed`), so a picker never offers a code the server would refuse.

Both lists are filtered by ``entity_permissions.entity_read_scope``, the rule
``epm_batch`` applies to every read: an unrestricted reader sees everything, a
restricted one sees the codes in ``allowed_entity_codes()``. A group is read
through its root row's entity (``data_area_id``), so it is listed only when
that entity is allowed. No caching: each call reads the current permissions.
"""
import frappe


def _allowed():
    from konsol.entity_permissions import allowed_entity_codes

    return allowed_entity_codes()


def _readable(code, allowed):
    from konsol.entity_permissions import entity_read_scope

    _, error = entity_read_scope(code, allowed)
    return error is None


def entities():
    """Entities the current user may read, sorted by code."""
    allowed = _allowed()
    rows = frappe.get_all(
        "Entity",
        fields=["entity_code", "entity_name", "is_group", "functional_currency", "status"],
        order_by="entity_code asc",
        limit_page_length=0,
    )
    return [
        {
            "code": r.entity_code,
            "name": r.entity_name,
            "is_group": bool(r.is_group),
            "currency": r.functional_currency,
            "status": r.status,
        }
        for r in rows
        if _readable(r.entity_code, allowed)
    ]


def groups():
    """Distinct Consolidation Group codes the current user may read, sorted.

    Members share their root's code, so each code appears once. The name and
    the entity come from the root row (the one with no parent); a code with no
    root row, which should not exist, falls back to its first row.
    """
    allowed = _allowed()
    rows = frappe.get_all(
        "Consolidation Group",
        fields=["consolidation_group", "entity_name", "data_area_id",
                "parent_consolidation_group"],
        order_by="consolidation_group asc, name asc",
        limit_page_length=0,
    )
    by_code = {}
    for r in rows:
        current = by_code.get(r.consolidation_group)
        is_root = not r.parent_consolidation_group
        if current is None or (is_root and current.get("_root") is False):
            by_code[r.consolidation_group] = {
                "code": r.consolidation_group,
                "name": r.entity_name,
                "entity": r.data_area_id,
                "_root": is_root,
            }
    return [
        {"code": g["code"], "name": g["name"]}
        for _, g in sorted(by_code.items())
        if _readable(g["entity"], allowed)
    ]


def is_allowed(kind, code):
    """True when ``code`` is in the list for ``kind`` ("entity" or "group")
    and the current user may read it. An unknown kind is a caller bug, so it
    raises rather than answering False."""
    if kind == "entity":
        listed = entities()
    elif kind == "group":
        listed = groups()
    else:
        raise ValueError(f"Unknown report choice kind '{kind}'")
    return any(item["code"] == code for item in listed)

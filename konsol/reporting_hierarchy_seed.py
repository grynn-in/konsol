"""Flatten Reporting Hierarchy Member trees into reporting_hierarchies.csv rows."""
from __future__ import annotations


def flatten_reporting_hierarchies(frappe):
    """Return seed rows for all Published Reporting Hierarchy headers."""
    headers = frappe.get_all(
        "Reporting Hierarchy",
        filters={"status": "Published"},
        fields=[
            "name",
            "hierarchy_name",
            "dimension",
            "effective_from",
            "effective_to",
            "is_default",
        ],
        order_by="hierarchy_name asc",
        limit_page_length=0,
    )
    if not headers:
        return []

    rows = []
    for header in headers:
        members = frappe.get_all(
            "Reporting Hierarchy Member",
            filters={"reporting_hierarchy": header.name},
            fields=["name", "parent_member", "member_code", "member_label", "is_group"],
            order_by="member_code asc",
            limit_page_length=0,
        )
        by_name = {m.name: m for m in members}
        for member in members:
            ancestors = _ancestor_chain(member, by_name)
            level = len(ancestors) + 1
            path_parts = [
                by_name[a].member_code for a in reversed(ancestors) if by_name[a].member_code
            ]
            if member.member_code:
                path_parts.append(member.member_code)
            parent_code = ""
            if member.parent_member and member.parent_member in by_name:
                parent_code = by_name[member.parent_member].member_code or ""
            rows.append({
                "hierarchy_name": header.hierarchy_name,
                "dimension": header.dimension,
                "member_code": member.member_code or "",
                "member_label": member.member_label or "",
                "parent_member_code": parent_code,
                "is_group": 1 if member.is_group else 0,
                "hierarchy_level": level,
                "path": "/".join(path_parts),
                "effective_from": str(header.effective_from or "2024-01-01"),
                "effective_to": str(header.effective_to or "9999-12-31"),
                "is_default": 1 if header.is_default else 0,
                "status": "Published",
            })
    return rows


def _ancestor_chain(member, by_name):
    """Walk parent_member links to root; return ancestor names nearest-first."""
    ancestors = []
    current = member.parent_member
    seen = set()
    while current and current in by_name and current not in seen:
        seen.add(current)
        ancestors.append(current)
        current = by_name[current].parent_member
    return ancestors
"""Flatten Reporting Hierarchy Member trees into reporting_hierarchies.csv rows."""
from __future__ import annotations

# The warehouse's open end (ClickHouse Date32 max); a blank effective_to.
OPEN_END = "2299-12-31"


def flatten_reporting_hierarchies(frappe):
    """Return seed rows for all Published Reporting Hierarchy headers.

    One row per member row, i.e. per dated TRANCHE of a member code: each row
    carries that tranche's own label and window as ``member_effective_from`` /
    ``member_effective_to`` (ISO dates; a blank end is ``OPEN_END``).
    ``parent_member_code`` is the linked parent's code. ``path`` and
    ``hierarchy_level`` follow the chain of linked parent rows and are
    informational only: the warehouse resolves the tree per period from codes
    and dates, not from this path.
    """
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
            fields=[
                "name",
                "parent_member",
                "member_code",
                "member_label",
                "is_group",
                "effective_from",
                "effective_to",
            ],
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
                "member_effective_from": _iso(member.effective_from) or "1900-01-01",
                "member_effective_to": _iso(member.effective_to) or OPEN_END,
            })
    return rows


def _iso(value):
    """A Date field value (date or string) as YYYY-MM-DD; '' when blank."""
    if not value:
        return ""
    return str(value)[:10]


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

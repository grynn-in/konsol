"""A hierarchy node must resolve to exactly one tree and one member.

The resolver used to take the most recently edited tree holding the node, so
a formula with no hierarchy name could change value when someone edited a
different tree. Now: blank hierarchy + node in several published trees is
an error naming them; a code shared by two members of one tree is an error.
"""
import sys
import types

from konsol.hierarchy_query import choose_hierarchy, choose_member


def test_one_tree_is_the_answer():
    assert choose_hierarchy("DACH", ["MGMT_2026"]) == ("MGMT_2026", None)


def test_several_trees_is_an_error_naming_them():
    name, err = choose_hierarchy("DACH", ["MGMT_2026", "MGMT_2027"])
    assert name is None
    assert "DACH" in err and "MGMT_2026, MGMT_2027" in err
    assert "Pass the hierarchy name" in err


def test_no_tree_is_an_error():
    name, err = choose_hierarchy("NOPE", [])
    assert name is None and "not in any published" in err


def test_member_shared_code_is_an_error():
    rows = [{"member_code": "DACH", "member_label": "DACH", "is_group": 1},
            {"member_code": "DACH", "member_label": "Dach (old)", "is_group": 1}]
    member, err = choose_member("DACH", "MGMT_2026", rows)
    assert member is None
    assert "2 members" in err and "DACH, Dach (old)" in err


def test_member_single_and_missing():
    row = {"member_code": "BU1", "member_label": "BU 1", "is_group": 0}
    assert choose_member("BU1", "H", [row]) == (row, None)
    assert "not found" in choose_member("BU9", "H", [])[1]


def _fake_frappe(members, trees):
    """Just enough frappe for resolve_hierarchy_name's blank-name path."""
    def get_all(doctype, filters=None, pluck=None, **_kw):
        if doctype == "Reporting Hierarchy Member":
            return [m["tree"] for m in members if m["code"] == filters["member_code"]]
        wanted = filters["name"][1]
        return sorted(t for t, status in trees.items()
                      if t in wanted and status == filters["status"])
    return types.SimpleNamespace(get_all=get_all)


def _resolve(members, trees, node):
    from konsol.hierarchy_query import resolve_hierarchy_name
    saved = sys.modules.get("frappe")
    sys.modules["frappe"] = _fake_frappe(members, trees)
    try:
        return resolve_hierarchy_name("", node)
    finally:
        if saved is None:
            sys.modules.pop("frappe", None)
        else:
            sys.modules["frappe"] = saved


def test_resolve_blank_hierarchy():
    members = [
        {"tree": "MGMT_2026", "code": "DACH"},
        {"tree": "MGMT_2027", "code": "DACH"},
        {"tree": "MGMT_2026", "code": "BU_DE"},
        {"tree": "OLD_DRAFT", "code": "BU_AT"},
    ]
    trees = {"MGMT_2026": "Published", "MGMT_2027": "Published", "OLD_DRAFT": "Draft"}
    assert _resolve(members, trees, "BU_DE") == ("MGMT_2026", None)
    name, err = _resolve(members, trees, "DACH")
    assert name is None and "MGMT_2026, MGMT_2027" in err
    # only in an unpublished tree: an error, not a fall-back to some default
    name, err = _resolve(members, trees, "BU_AT")
    assert name is None and "not in any published" in err

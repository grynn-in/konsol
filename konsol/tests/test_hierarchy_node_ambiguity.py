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
    """Just enough frappe for resolve_hierarchy_name. Matches codes and names
    case-insensitively, as MariaDB's collation does."""
    def get_all(doctype, filters=None, pluck=None, **_kw):
        if doctype == "Reporting Hierarchy Member":
            code = filters["member_code"].lower()
            return [m["tree"] for m in members if m["code"].lower() == code]
        wanted = filters["name"][1]
        return sorted(t for t, status in trees.items()
                      if t in wanted and status == filters["status"])

    def get_value(doctype, filters, field):
        name = filters["hierarchy_name"].lower()
        return next((t for t, status in trees.items()
                     if t.lower() == name and status == filters["status"]), None)

    return types.SimpleNamespace(get_all=get_all, db=types.SimpleNamespace(get_value=get_value))


def _with_frappe(fake, fn):
    saved = sys.modules.get("frappe")
    sys.modules["frappe"] = fake
    try:
        return fn()
    finally:
        if saved is None:
            sys.modules.pop("frappe", None)
        else:
            sys.modules["frappe"] = saved


def _resolve(members, trees, node, hierarchy=""):
    from konsol.hierarchy_query import resolve_hierarchy_name
    return _with_frappe(_fake_frappe(members, trees),
                        lambda: resolve_hierarchy_name(hierarchy, node))


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
    # MariaDB matches a lower-case node code too
    assert _resolve(members, trees, "bu_de") == ("MGMT_2026", None)


def test_named_hierarchy_returns_stored_spelling():
    trees = {"MGMT_2026": "Published", "OLD_DRAFT": "Draft"}
    # ClickHouse compares case-sensitively, so the stored name must go on
    assert _resolve([], trees, "DACH", "mgmt_2026") == ("MGMT_2026", None)
    name, err = _resolve([], trees, "DACH", "OLD_DRAFT")
    assert name is None and "not found or not published" in err


class _Thrown(Exception):
    pass


def _member_controller(existing):
    """Load the member controller against a stub frappe, under a private
    module name so the real one in sys.modules is never replaced by a
    stub-bound copy. ``existing`` maps (tree, code) to the name of a member
    already holding that code."""
    import importlib.util
    import os

    calls = []

    def exists(doctype, filters):
        calls.append(filters)
        hit = existing.get((filters["reporting_hierarchy"], filters["member_code"]))
        return hit if hit and hit != filters["name"][1] else None

    def throw(msg, *a, **k):
        raise _Thrown(msg)

    document = types.ModuleType("frappe.model.document")
    document.Document = object
    fake = types.ModuleType("frappe")
    fake.db = types.SimpleNamespace(exists=exists)
    fake.throw = throw
    fake.scrub = lambda s: s.strip().lower().replace(" ", "_").replace("-", "_")
    mods = {"frappe": fake, "frappe.model": types.ModuleType("frappe.model"),
            "frappe.model.document": document}
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "epm", "doctype", "reporting_hierarchy_member",
                        "reporting_hierarchy_member.py")
    spec = importlib.util.spec_from_file_location("_stub_reporting_hierarchy_member", path)
    mod = importlib.util.module_from_spec(spec)
    saved = {k: sys.modules.get(k) for k in mods}
    sys.modules.update(mods)
    try:
        spec.loader.exec_module(mod)
    finally:
        for k, v in saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v
    return mod, fake, calls


def _member(mod, **fields):
    m = mod.ReportingHierarchyMember.__new__(mod.ReportingHierarchyMember)
    base = {"name": "new1", "reporting_hierarchy": "MGMT_2026", "member_code": "",
            "member_label": "", "is_group": 0}
    base.update(fields)
    m.__dict__.update(base)
    return m


def _validate(mod, fake, member):
    saved = sys.modules.get("frappe")
    sys.modules["frappe"] = fake
    mod.frappe = fake
    try:
        member._validate_member_code()
    finally:
        if saved is None:
            sys.modules.pop("frappe", None)
        else:
            sys.modules["frappe"] = saved


def test_group_code_must_be_unique_in_its_tree():
    mod, fake, calls = _member_controller({("MGMT_2026", "DACH"): "abc123"})
    try:
        _validate(mod, fake, _member(mod, is_group=1, member_code="DACH", member_label="DACH"))
        raise AssertionError("duplicate group code saved")
    except _Thrown as e:
        assert "already exists" in str(e) and "abc123" in str(e)
    # the check excludes the member itself, so re-saving it is fine
    _validate(mod, fake, _member(mod, name="abc123", is_group=1, member_code="DACH",
                                 member_label="DACH renamed"))
    assert calls[-1]["name"] == ["!=", "abc123"]


def test_group_auto_code_and_blank_label():
    mod, fake, _ = _member_controller({})
    m = _member(mod, is_group=1, member_label="Other regions")
    _validate(mod, fake, m)
    assert m.member_code == "OTHER_REGIONS"
    # no label and no code: left for Frappe's "Member Label is mandatory"
    blank = _member(mod, is_group=1)
    _validate(mod, fake, blank)
    assert blank.member_code == ""


def test_leaf_needs_a_code():
    mod, fake, _ = _member_controller({})
    try:
        _validate(mod, fake, _member(mod, member_label="BU 1"))
        raise AssertionError("leaf without code saved")
    except _Thrown as e:
        assert "required for leaf nodes" in str(e)

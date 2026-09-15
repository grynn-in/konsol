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
    stub-bound copy. ``existing`` is a list of member rows already saved
    (name, reporting_hierarchy, member_code, effective_from, effective_to)."""
    import importlib.util
    import os

    calls = []

    def _hits(filters):
        calls.append(filters)
        return [r for r in existing
                if r["reporting_hierarchy"] == filters["reporting_hierarchy"]
                and r["member_code"] == filters["member_code"]
                and r["name"] != filters["name"][1]]

    def exists(doctype, filters):
        hits = _hits(filters)
        return hits[0]["name"] if hits else None

    def get_all(doctype, filters=None, fields=None, **_kw):
        return [types.SimpleNamespace(**{f: r.get(f) for f in fields or ["name"]})
                for r in _hits(filters)]

    def throw(msg, *a, **k):
        raise _Thrown(msg)

    document = types.ModuleType("frappe.model.document")
    document.Document = object
    fake = types.ModuleType("frappe")
    fake.db = types.SimpleNamespace(exists=exists)
    fake.get_all = get_all
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
            "member_label": "", "is_group": 0, "effective_from": "2017-01-01",
            "effective_to": None}
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
    """A code identifies one node per period (konsol#220): K.EPM's node
    argument and the warehouse rollup find a node by code and date, so two
    rows of one code may not overlap. Adjacent rows (a rename) are fine."""
    held = {"name": "abc123", "reporting_hierarchy": "MGMT_2026", "member_code": "DACH",
            "effective_from": "2017-01-01", "effective_to": "2024-12-31"}
    mod, fake, calls = _member_controller([held])
    try:
        _validate(mod, fake, _member(mod, is_group=1, member_code="DACH",
                                     member_label="DACH", effective_from="2024-06-01"))
        raise AssertionError("overlapping group code saved")
    except _Thrown as e:
        assert "already has a row covering" in str(e) and "(abc123)" in str(e)
    # the next tranche of the code starts the day after the old one ends
    _validate(mod, fake, _member(mod, is_group=1, member_code="DACH",
                                 member_label="DACH renamed", effective_from="2025-01-01"))
    # the check excludes the member itself, so re-saving it is fine
    _validate(mod, fake, _member(mod, name="abc123", is_group=1, member_code="DACH",
                                 member_label="DACH renamed", effective_from="2017-01-01",
                                 effective_to="2024-12-31"))
    assert calls[-1]["name"] == ["!=", "abc123"]


def test_group_auto_code_and_blank_label():
    mod, fake, _ = _member_controller([])
    m = _member(mod, is_group=1, member_label="Other regions")
    _validate(mod, fake, m)
    assert m.member_code == "OTHER_REGIONS"
    # no label and no code: left for Frappe's "Member Label is mandatory"
    blank = _member(mod, is_group=1)
    _validate(mod, fake, blank)
    assert blank.member_code == ""


def test_leaf_needs_a_code():
    mod, fake, _ = _member_controller([])
    try:
        _validate(mod, fake, _member(mod, member_label="BU 1"))
        raise AssertionError("leaf without code saved")
    except _Thrown as e:
        assert "required for leaf nodes" in str(e)


# konsol#220 row R7: a renamed node is two dated tranches of one code. A
# formula or a budget write names the code, so it reads the tranche of today.

_OLD = {"name": "m1", "member_code": "ZZ_A", "member_label": "ZZ A old", "is_group": 1,
        "effective_from": "2017-01-01", "effective_to": "2024-12-31"}
_NEW = {"name": "m2", "member_code": "ZZ_A", "member_label": "ZZ A new", "is_group": 1,
        "effective_from": "2025-01-01", "effective_to": None}


def test_current_tranche_covers_today():
    from konsol.hierarchy_query import current_tranche
    assert current_tranche([_OLD, _NEW], "2025-06-30") is _NEW
    assert current_tranche([_OLD, _NEW], "2024-06-30") is _OLD
    # bounds are inclusive
    assert current_tranche([_OLD, _NEW], "2024-12-31") is _OLD
    assert current_tranche([_OLD, _NEW], "2025-01-01") is _NEW


def test_current_tranche_blank_dates_and_all_ended():
    from konsol.hierarchy_query import current_tranche
    undated = {"member_label": "undated", "effective_from": None, "effective_to": None}
    assert current_tranche([undated], "2025-06-30") is undated
    early = {"member_label": "early", "effective_from": "2010-01-01", "effective_to": "2015-12-31"}
    late = {"member_label": "late", "effective_from": "2016-01-01", "effective_to": "2020-12-31"}
    # every tranche ended: the latest one
    assert current_tranche([late, early], "2024-06-30") is late
    assert current_tranche([], "2024-06-30") is None


def _member_frappe(rows, today):
    class _D(dict):
        __getattr__ = dict.get

    def get_value(doctype, filters, fields, as_dict=False):
        return _D(name="ZZ_H", dimension="business_unit", status="Published")

    def get_all(doctype, filters=None, fields=None, **_kw):
        return [_D({f: r.get(f) for f in fields}) for r in rows
                if r["member_code"] == filters["member_code"]]

    utils = types.ModuleType("frappe.utils")
    utils.today = lambda: today
    return types.SimpleNamespace(get_all=get_all, utils=utils,
                                 db=types.SimpleNamespace(get_value=get_value))


def test_get_hierarchy_member_reads_the_tranche_of_today():
    from konsol.hierarchy_query import get_hierarchy_member
    info, err = _with_frappe(_member_frappe([_OLD, _NEW], "2025-06-30"),
                             lambda: get_hierarchy_member("ZZ_H", "ZZ_A"))
    assert err is None, err
    assert info["member_label"] == "ZZ A new" and info["member_code"] == "ZZ_A"
    info, err = _with_frappe(_member_frappe([_OLD, _NEW], "2024-06-30"),
                             lambda: get_hierarchy_member("ZZ_H", "ZZ_A"))
    assert err is None and info["member_label"] == "ZZ A old"
    info, err = _with_frappe(_member_frappe([_OLD, _NEW], "2025-06-30"),
                             lambda: get_hierarchy_member("ZZ_H", "ZZ_NONE"))
    assert info is None and "not found" in err


# konsol#220 row R11: a budget write lands in one fiscal period, so the node
# is checked as it is on that period's end date, not as it is today.

_PERIOD_ENDS = {(2026, 3): "2026-03-31", (2024, 6): "2024-06-30",
                (2023, 6): "2023-06-30", (2025, 6): "2025-06-30"}

_EX = {"name": "e1", "member_code": "ZZ_EX", "member_label": "ZZ EX", "is_group": 0,
       "effective_from": "2017-01-01", "effective_to": "2024-12-31"}
_Y_LEAF = {"name": "y1", "member_code": "ZZ_Y", "member_label": "ZZ Y", "is_group": 0,
           "effective_from": "2020-01-01", "effective_to": "2023-12-31"}
_Y_GROUP = {"name": "y2", "member_code": "ZZ_Y", "member_label": "ZZ Y", "is_group": 1,
            "effective_from": "2024-01-01", "effective_to": None}


def _write_frappe(rows, today="2025-06-30"):
    """frappe for validate_hierarchy_write: the named tree ZZ_H is published."""
    class _D(dict):
        __getattr__ = dict.get

    def get_value(doctype, filters, fields, as_dict=False):
        if fields == "hierarchy_name":
            return "ZZ_H"
        return _D(name="ZZ_H", dimension="business_unit", status="Published")

    def get_all(doctype, filters=None, fields=None, **_kw):
        return [_D({f: r.get(f) for f in fields}) for r in rows
                if r["member_code"] == filters["member_code"]]

    utils = types.ModuleType("frappe.utils")
    utils.today = lambda: today
    return types.SimpleNamespace(get_all=get_all, utils=utils,
                                 db=types.SimpleNamespace(get_value=get_value))


def _write(rows, node, fy, fp):
    from konsol.hierarchy_query import validate_hierarchy_write

    status = types.ModuleType("konsol.period_status")
    status.period_dates = lambda y, p: ("start", _PERIOD_ENDS[(y, p)])
    grain = types.ModuleType("konsol.epm.budget_grain")
    grain.budget_dimension_names = lambda: ["business_unit"]
    stubs = {"konsol.period_status": status, "konsol.epm.budget_grain": grain}
    saved = {k: sys.modules.get(k) for k in stubs}
    sys.modules.update(stubs)
    try:
        return _with_frappe(_write_frappe(rows), lambda: validate_hierarchy_write(
            "ZZ_H", node, fiscal_year=fy, fiscal_period=fp))
    finally:
        for k, v in saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v


def test_write_to_a_period_after_the_leaf_ended_is_refused():
    info, err = _write([_EX], "ZZ_EX", 2026, 3)
    assert info is None
    assert err == "Node 'ZZ_EX' is not in hierarchy 'ZZ_H' on 2026-03-31 (FY2026 P3)."


def test_write_to_a_period_the_leaf_covers_is_accepted():
    info, err = _write([_EX], "ZZ_EX", 2024, 6)
    assert err is None, err
    assert info["member_code"] == "ZZ_EX" and info["dimension"] == "business_unit"


def test_write_checks_leaf_or_group_in_that_period():
    # ZZ_Y was a leaf to 2023 and is a group from 2024 (today it is a group)
    info, err = _write([_Y_LEAF, _Y_GROUP], "ZZ_Y", 2023, 6)
    assert err is None, err
    assert info["is_group"] is False
    info, err = _write([_Y_LEAF, _Y_GROUP], "ZZ_Y", 2025, 6)
    assert info is None and "is a group" in err


def test_budget_write_passes_its_period():
    import os
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "api.py")
    block = open(path).read().split("def budget_cell_save")[1].split("\n@frappe.whitelist")[0]
    call = block.split("validate_hierarchy_write(")[1].split(")\n")[0]
    assert 'fiscal_year=int(data["fiscal_year"])' in call
    assert "fiscal_period=fp" in call

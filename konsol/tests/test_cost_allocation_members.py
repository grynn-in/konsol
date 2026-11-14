"""KF2: dimension members for cost allocation, read without v16's 20-row page
cap and without the user's Dimension Value permission."""
import importlib.util
import os
import sys
import types

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HELPER = os.path.join(APP_DIR, "cost_allocation_members.py")
DV_PKG = "konsol.epm.doctype.dimension_value"


def _helper(rows, calls, failed=False):
    """Load the helper with a fake dimension_value controller."""
    dv = types.ModuleType(DV_PKG + ".dimension_value")
    dv._rows = lambda: (calls.append("_rows") or rows)

    def refuse(*a, **k):
        raise AssertionError("per-user read check or paged list used")

    dv._read_failed = lambda: failed
    dv._assert_read = refuse
    dv.DimensionValue = types.SimpleNamespace(get_list=refuse, get_count=refuse)
    chain = {}
    for name in ("konsol.epm", "konsol.epm.doctype", DV_PKG):
        m = types.ModuleType(name)
        m.__path__ = []
        chain[name] = m
    chain[DV_PKG].dimension_value = dv
    chain[DV_PKG + ".dimension_value"] = dv
    saved = {k: sys.modules.get(k) for k in chain}
    sys.modules.update(chain)
    try:
        spec = importlib.util.spec_from_file_location("caj_members_under_test", HELPER)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        yield mod
    finally:
        for k, v in saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v


def _rows(n, dimension="ZZDIM"):
    return [{"name": f"{dimension}:ZZM{i:02d}", "dimension": dimension, "value": f"ZZM{i:02d}"}
            for i in range(1, n + 1)]


def test_the_25th_value_is_a_member():
    rows = _rows(25) + _rows(3, "OTHER")
    calls = []
    for mod in _helper(rows, calls):
        members = mod.dimension_members("ZZDIM")
    assert "ZZM25" in members
    assert len(members) == 25
    assert calls == ["_rows"]


def test_members_do_not_depend_on_the_users_permission():
    # the fake's _assert_read, get_list and get_count all raise
    for mod in _helper(_rows(21), []):
        assert len(mod.dimension_members("ZZDIM")) == 21


def test_an_undeclared_dimension_has_no_members():
    for mod in _helper(_rows(3), []):
        assert mod.dimension_members("NOPE") == set()


def test_rule_set_reads_members_through_the_helper_only():
    path = os.path.join(APP_DIR, "epm", "doctype", "cost_allocation_rule_set",
                        "cost_allocation_rule_set.py")
    src = open(path).read()
    assert "cost_allocation_members" in src
    assert '"Dimension Value"' not in src


def test_an_empty_read_that_failed_raises_a_distinct_error():
    for mod in _helper([], [], failed=True):
        try:
            mod.dimension_members("ZZDIM")
        except mod.MemberReadError as exc:
            assert "could not read the members of ZZDIM" in str(exc)
            assert "Error Log" in str(exc)
        else:
            raise AssertionError("expected MemberReadError")


def test_an_empty_read_that_did_not_fail_is_an_empty_set():
    for mod in _helper([], [], failed=False):
        assert mod.dimension_members("ZZDIM") == set()

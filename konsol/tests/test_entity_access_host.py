"""entity_permissions.assert_entity_access, exercised rather than read.

The ClickHouse read paths (K.EPM, hierarchy reads, budget reads) bypass
Frappe's permission layer, so this one function is what stops a user
limited to some entities from reading the others. Loaded under a private
module name with a stub frappe, so it runs on any host.
"""
import importlib.util
import os
import sys
import types

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class _Denied(Exception):
    pass


def _load(allowed):
    """entity_permissions with allowed_entity_codes() fixed to ``allowed``
    (a set of codes, or None for an unrestricted user)."""
    fake = types.ModuleType("frappe")
    fake.PermissionError = _Denied

    def throw(msg, exc=Exception, *a, **k):
        raise exc(msg)

    fake.throw = throw
    before = set(sys.modules)
    saved = sys.modules.get("frappe")
    sys.modules["frappe"] = fake
    try:
        spec = importlib.util.spec_from_file_location(
            "_host_entity_permissions", os.path.join(APP_DIR, "entity_permissions.py"))
        mod = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(mod)
        except ImportError as e:
            # A security pin must fail, not be counted as "needs frappe".
            raise AssertionError(
                f"entity_permissions needs more than a stub frappe at import: {e}")
    finally:
        for key in set(sys.modules) - before:
            if key.split(".")[0] in ("frappe", "konsol"):
                del sys.modules[key]
        if saved is None:
            sys.modules.pop("frappe", None)
        else:
            sys.modules["frappe"] = saved
    mod.allowed_entity_codes = lambda user=None: allowed
    return mod


def _raises(fn, *args):
    try:
        fn(*args)
    except _Denied:
        return True
    return False


def test_restricted_user_is_refused_other_entities():
    ep = _load({"DE01", "AT01"})
    assert not _raises(ep.assert_entity_access, "DE01")
    assert _raises(ep.assert_entity_access, "US01")


def test_user_with_no_entities_is_refused_everything():
    ep = _load(set())
    assert _raises(ep.assert_entity_access, "DE01")


def test_unrestricted_user_passes():
    ep = _load(None)
    assert not _raises(ep.assert_entity_access, "US01")


# ── entity_read_scope: the one rule every ClickHouse read asks (konsol#158) ──

_REFUSED_US01 = "Not permitted to access entity 'US01'"
_REFUSED_ANY = "Not permitted to access any entity"


def _scope(allowed, entity, wildcard=False):
    return _load(allowed).entity_read_scope(entity, allowed, wildcard=wildcard)


def test_scope_single_entity_for_a_restricted_reader():
    assert _scope({"DE01", "AT01"}, "DE01") == (None, None)
    assert _scope({"DE01", "AT01"}, "US01") == (None, _REFUSED_US01)


def test_scope_blank_entity_is_refused_to_a_restricted_reader():
    # A blank entity is not an entity the reader may see: gold variance
    # tables hold rows with data_area_id = '', and a restricted reader must
    # not reach them by leaving the entity out.
    assert _scope({"DE01"}, "") == (None, "Not permitted to access entity ''")
    assert _scope({"DE01"}, None)[1] is not None
    assert _raises(_load({"DE01"}).assert_entity_access, "")


def test_scope_wildcard_is_limited_to_the_allowed_set():
    assert _scope({"DE01", "AT01"}, "ALL", wildcard=True) == (["AT01", "DE01"], None)


def test_scope_with_no_entities_reads_nothing():
    assert _scope(set(), "ALL", wildcard=True) == (None, _REFUSED_ANY)
    assert _scope(set(), "DE01") == (None, "Not permitted to access entity 'DE01'")


def test_scope_unrestricted_reader_sees_everything():
    for entity, wildcard in (("US01", False), ("", False), ("ALL", True)):
        assert _scope(None, entity, wildcard=wildcard) == (None, None)
    assert not _raises(_load(None).assert_entity_access, "")


def test_scope_needs_the_allow_list():
    # No default: a caller that forgets it must fail, not read every entity.
    ep = _load(None)
    try:
        ep.entity_read_scope("DE01")
    except TypeError:
        return
    raise AssertionError("entity_read_scope(entity) without allowed must raise TypeError")


def test_assert_entity_access_refuses_with_the_helpers_message():
    ep = _load({"DE01"})
    try:
        ep.assert_entity_access("US01")
    except _Denied as e:
        assert str(e) == _REFUSED_US01
    else:
        raise AssertionError("US01 was not refused")


# ── the wildcard hierarchy read applies the helper's scope in its SQL ────────

def _hierarchy(allowed_entities, entity="ALL"):
    """Run hierarchy_query.batch_query_hierarchy for one row against a fake
    ClickHouse. Returns (result, [(sql, params), ...])."""
    ep = _load(allowed_entities)
    spec = importlib.util.spec_from_file_location(
        "_host_hierarchy_query", os.path.join(APP_DIR, "hierarchy_query.py"))
    hq = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(hq)  # needs requests, like test_hierarchy_query
    queries = []

    def fake_query(sql, params, ch_settings):
        queries.append((sql, dict(params)))
        return ""

    hq._clickhouse_query = fake_query
    stubs = {
        "konsol.clickhouse": types.SimpleNamespace(get_connection=lambda: {}),
        "konsol.entity_permissions": ep,
    }
    saved = {k: sys.modules.get(k) for k in stubs}
    sys.modules.update(stubs)
    try:
        result = hq.batch_query_hierarchy([{
            "entity": entity, "year": 2024, "periods": (1,), "account": "4010",
            "scenario": "actuals", "hierarchy_name": "H", "hierarchy_node": "N",
        }], allowed_entities=allowed_entities)
    finally:
        for k, mod in saved.items():
            if mod is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = mod
    return result, queries


def test_wildcard_hierarchy_read_is_limited_in_sql():
    result, queries = _hierarchy({"DE01", "AT01"})
    assert not result.get("errors")
    (sql, params), = queries
    assert "AND data_area_id IN ({ent0:String}, {ent1:String})" in sql
    assert (params["param_ent0"], params["param_ent1"]) == ("AT01", "DE01")


def test_wildcard_hierarchy_read_with_no_entities_issues_no_query():
    result, queries = _hierarchy(set())
    assert queries == []
    assert result["errors"] == [_REFUSED_ANY]
    assert result["values"] == [None]


def test_wildcard_hierarchy_read_unrestricted_has_no_entity_filter():
    result, queries = _hierarchy(None)
    (sql, _), = queries
    assert "data_area_id IN" not in sql
    assert result["values"] == [0.0]

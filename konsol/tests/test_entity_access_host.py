"""entity_permissions.assert_entity_access, exercised rather than read.

The ClickHouse read paths (K.EPM, hierarchy reads, budget reads) bypass
Frappe's permission layer, so this one function is what stops a user
limited to some entities from reading the others. Loaded under a private
module name with a stub frappe, so it runs on any host.
"""
import contextlib
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

def _load_hq():
    """hierarchy_query under a private name (it imports no frappe at load)."""
    spec = importlib.util.spec_from_file_location(
        "_host_hierarchy_query", os.path.join(APP_DIR, "hierarchy_query.py"))
    hq = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(hq)  # needs requests, like test_hierarchy_query
    return hq


def _hierarchy(allowed_entities, entity="ALL"):
    """Run hierarchy_query.batch_query_hierarchy for one row against a fake
    ClickHouse. Returns (result, [(sql, params), ...])."""
    ep = _load(allowed_entities)
    hq = _load_hq()
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


# ── the endpoints: a refused entity never reaches a query (#163 review) ──────
#
# The checks above prove the rule; these prove epm_value and epm_batch act on
# it. api.py is loaded under a private name on a stub frappe and a stub
# konsol.clickhouse, so this runs on any host and in CI. Only the query
# functions and the Frappe-backed lookups are stubbed; the entity checks run
# for real, through entity_permissions loaded as above.

class _Invalid(Exception):
    pass


def _load_api():
    fake = types.ModuleType("frappe")
    fake.PermissionError = _Denied
    fake.ValidationError = _Invalid

    def throw(msg, exc=Exception, *a, **k):
        raise exc(msg)

    fake.throw = throw
    fake.whitelist = lambda *a, **k: (lambda fn: fn)
    utils = types.ModuleType("frappe.utils")
    utils.now_datetime = lambda: None
    fake.utils = utils
    ch = types.ModuleType("konsol.clickhouse")
    ch.connection_url = lambda settings: ""
    ch.get_connection = lambda: {}
    with _modules({"frappe": fake, "frappe.utils": utils, "konsol.clickhouse": ch}):
        spec = importlib.util.spec_from_file_location("_host_api", os.path.join(APP_DIR, "api.py"))
        api = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(api)
        except ImportError as e:
            raise AssertionError(f"api.py needs more than the stubs at import: {e}")
    return api


@contextlib.contextmanager
def _modules(stubs):
    saved = {k: sys.modules.get(k) for k in stubs}
    sys.modules.update(stubs)
    try:
        yield
    finally:
        for k, mod in saved.items():
            if mod is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = mod


class _Endpoints:
    """epm_value / epm_batch for a reader whose allow-list is ``allowed``.
    ``flat`` and ``hier`` record the entities that reached each query."""

    def __init__(self, allowed):
        self.api, self.ep, self.hq = _load_api(), _load(allowed), _load_hq()
        self.flat, self.hier = [], []
        api, hq = self.api, self.hq
        # api._allowed_entities is NOT stubbed: the real one reads the private
        # entity_permissions below, whose allowed_entity_codes is fixed.
        api._resolve_and_validate = lambda fact, scenario, measure, dims: (
            types.SimpleNamespace(fact_name="f"), None)

        def flat_query(reqs):
            self.flat.extend(r["entity"] for r in reqs)
            return {"values": [1.0] * len(reqs)}

        def hier_query(reqs, *, allowed_entities):
            self.hier.extend((r["entity"], allowed_entities) for r in reqs)
            return {"values": [2.0] * len(reqs)}

        api._batch_query_clickhouse = flat_query
        hq.batch_query_hierarchy = hier_query
        hq.validate_hierarchy_read = lambda name, node, scenario: (
            {"hierarchy_name": "H", "member_code": node}, None)

    def _run(self, fn, *a, **k):
        with _modules({"konsol.entity_permissions": self.ep, "konsol.hierarchy_query": self.hq}):
            return fn(*a, **k)

    def value(self, entity, **k):
        return self._run(self.api.epm_value, entity, 2024, "FY", "4010", **k)

    def batch(self, rows):
        self.api._get_json_body = lambda: rows
        return self._run(self.api.epm_batch)


def _row(entity, node=None):
    r = {"entity": entity, "year": 2024, "period": "FY", "account": "4010"}
    if node:
        r["hierarchy_node"] = node
    return r


def test_epm_value_refuses_a_forbidden_entity_before_any_query():
    e = _Endpoints({"DE01"})
    for mode in ({}, {"node": "N"}):
        try:
            e.value("US01", **mode)
        except _Denied as err:
            assert str(err) == _REFUSED_US01
        else:
            raise AssertionError(f"epm_value did not refuse US01 ({mode or 'flat'})")
    # A blank flat entity would query data_area_id = '' (gold variance tables
    # hold such rows). In hierarchy mode blank is the wildcard, tested below.
    try:
        e.value("")
    except _Denied as err:
        assert str(err) == "Not permitted to access entity ''"
    else:
        raise AssertionError("epm_value did not refuse a blank entity (flat)")
    assert e.flat == [] and e.hier == []


def test_epm_value_reads_a_permitted_entity_and_a_scoped_wildcard():
    e = _Endpoints({"DE01"})
    assert e.value("DE01") == {"value": 1.0}
    assert e.value("DE01", node="N") == {"value": 2.0}
    assert e.value("ALL", node="N") == {"value": 2.0}
    assert e.flat == ["DE01"]
    # The wildcard reaches the query with the allow-list, which limits it.
    assert e.hier == [("DE01", {"DE01"}), ("ALL", {"DE01"})]


def test_epm_batch_refuses_forbidden_rows_and_queries_only_the_rest():
    e = _Endpoints({"DE01"})
    out = e.batch([_row("DE01"), _row("US01"), _row("DE01", "N"), _row("US01", "N"),
                   _row("ALL", "N"), _row("")])
    assert out["errors"] == [None, _REFUSED_US01, None, _REFUSED_US01, None,
                             "Not permitted to access entity ''"]
    assert out["values"] == [1.0, None, 2.0, None, 2.0, None]
    assert e.flat == ["DE01"]
    assert e.hier == [("DE01", {"DE01"}), ("ALL", {"DE01"})]


def test_endpoints_let_an_unrestricted_reader_read_any_entity():
    e = _Endpoints(None)
    assert e.value("US01") == {"value": 1.0}
    assert e.value("US01", node="N") == {"value": 2.0}
    assert "errors" not in e.batch([_row("US01"), _row("US01", "N")])
    assert e.flat == ["US01", "US01"]
    assert [x for x, _ in e.hier] == ["US01", "US01"]


# ── test_security_source.py must load without frappe (#163 review) ──────────

def _loads_without_frappe(path):
    """Load a test file under a private name with frappe blocked and every
    konsol module unloaded, so an import that reaches frappe, directly or
    through konsol (konsol.clickhouse imports it), raises here. The runner
    would instead report the file as "needs frappe" and exit 0. Returns the
    ImportError, or None. Mirrored in test_security_source.py."""
    hidden = {k: sys.modules.pop(k) for k in list(sys.modules)
              if k.split(".")[0] in ("frappe", "konsol")}
    sys.modules["frappe"] = None
    try:
        spec = importlib.util.spec_from_file_location(
            "_frappe_free_" + os.path.basename(path)[:-3], path)
        spec.loader.exec_module(importlib.util.module_from_spec(spec))
        return None
    except ImportError as e:
        return e
    finally:
        for k in [k for k in sys.modules if k.split(".")[0] in ("frappe", "konsol")]:
            del sys.modules[k]
        sys.modules.update(hidden)


def test_security_source_loads_without_frappe():
    # CI has no frappe. If test_security_source.py needed it, the runner would
    # skip the whole file and still pass. A guard inside that file can never
    # run in that case, so it lives here, in a file that always loads.
    err = _loads_without_frappe(os.path.join(APP_DIR, "tests", "test_security_source.py"))
    assert err is None, (
        f"test_security_source.py cannot load without frappe ({err}); CI would skip it "
        "and none of its entity-access checks would run")

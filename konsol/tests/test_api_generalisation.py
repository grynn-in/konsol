"""Structural tests for Phase 2.4 — API generalisation (generic dimensions + fact param)."""
import ast
import importlib.util
import json
import os
import sys
import types

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
API_PATH = os.path.join(APP_DIR, "api.py")


def _content():
    with open(API_PATH) as f:
        return f.read()


def _funcs():
    return [n.name for n in ast.walk(ast.parse(_content())) if isinstance(n, ast.FunctionDef)]


def test_new_resolution_helpers_exist():
    for fn in ["_get_fact", "_published_measures", "_parse_dimensions_arg",
               "_resolve_and_validate"]:
        assert fn in _funcs(), f"Missing helper: {fn}"


def test_epm_value_accepts_fact_and_dimensions():
    sig = _content().split("def epm_value")[1].split("):")[0]
    assert "fact" in sig
    assert "dimensions" in sig


def test_epm_batch_reads_fact_param():
    content = _content()
    assert 'req.get("fact")' in content


def test_fact_wins_over_scenario_with_warning():
    """_get_fact prefers fact and logs when both are supplied."""
    content = _content()
    block = content.split("def _get_fact(")[1].split("def ")[0]
    assert "fact wins" in block.lower() or "warning" in block.lower()
    assert ".lower()" in block  # case-insensitive fact_name lookup


def test_measure_validated_against_published_registry():
    """Validation intersects the fact's measures with the Published Measure registry."""
    block = _content().split("def _resolve_and_validate")[1].split("\ndef ")[0]
    assert "_published_measures()" in block
    assert "_get_allowed_measures(fact)" in block


def test_dimensions_validated_against_fact():
    block = _content().split("def _resolve_and_validate")[1].split("\ndef ")[0]
    assert "_get_fact_dimensions(fact)" in block
    assert "Invalid dimension" in block


def test_no_dim_prefix_filtering():
    """Dimensions are taken from the explicit dict only — no dim_ prefix gating."""
    content = _content()
    assert 'startswith("dim_")' not in content


def test_no_legacy_dimension_params():
    """Legacy cost_center/department params and dim_* kwargs are gone."""
    content = _content()
    value_sig = content.split("def epm_value")[1].split("):")[0]
    assert "cost_center" not in value_sig
    assert "department" not in value_sig
    assert "**kwargs" not in value_sig


def test_grouping_keys_on_fact():
    block = _content().split("def _batch_query_clickhouse")[1]
    key_region = block.split("groups[key]")[0]
    assert 'req.get("fact")' in key_region
    assert "scenario_id" in key_region  # scenario_id retained in grouping


def test_no_allowed_measures_constant():
    """The hardcoded ALLOWED_MEASURES dict must not exist."""
    assert "ALLOWED_MEASURES" not in _content()


def test_legacy_validation_helpers_removed():
    """Backward-compat dropped: the old per-scenario validation helpers are gone."""
    content = _content()
    assert "_check_scenario" not in content
    assert "_check_measure" not in content
    assert "_validate_scenario_and_measure" not in content


def test_scenario_resolver_retained():
    """`scenario` still resolves a fact via scenario_key (a valid selector, not legacy)."""
    assert "_get_fact_by_scenario" in _funcs()


# ---------------------------------------------------------------------------
# konsol#105 Decision 1 — the default measure comes from the Dataset registry
#
# A read that names no measure used to be handed the literal "period_net_amount"
# before any Dataset was resolved, so it asked every dataset for a measure only
# three of the nine have (konsol#104). The measure is now filled from the
# resolved Dataset's default_measure, and a Dataset that declares none gives an
# error naming it — never a guess.
# ---------------------------------------------------------------------------


def test_fact_fields_load_the_default_measure():
    """A field missing from _FACT_FIELDS is silently absent on the fact doc."""
    fields_block = _content().split("_FACT_FIELDS")[1].split("]")[0]
    assert '"default_measure"' in fields_block


_READ_PATH_FUNCS = (
    "epm_value", "epm_batch", "_measure_for", "_hierarchy_measure",
    "default_measure_for_scenario", "_resolve_and_validate",
)


def _code_strings(fn_node):
    """Every string literal in ``fn_node`` that is code, docstrings excluded:
    prose naming a measure is not a measure the code substitutes."""
    docstrings = {
        n.value for n in ast.walk(fn_node)
        if isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant)
        and isinstance(n.value.value, str)
    }
    return [n.value for n in ast.walk(fn_node)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)
            and n not in docstrings]


def test_the_hardcoded_default_measure_is_gone():
    """No read-path function, and no module constant, substitutes a literal
    measure before a Dataset is resolved.

    Scoped to the read path and to code rather than the whole file: a
    docstring, an error message or a TB column name may legitimately say
    period_net_amount, and a test about a removed default must not fail on
    the word appearing anywhere in api.py.
    """
    content = _content()
    sig = content.split("def epm_value")[1].split("):")[0]
    assert 'measure="period_net_amount"' not in sig
    assert 'measure=""' in sig
    assert 'req.get("measure", "period_net_amount")' not in content

    tree = ast.parse(content)
    fns = {n.name: n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    for name in _READ_PATH_FUNCS:
        assert name in fns, f"read-path function {name} is gone"
        assert "period_net_amount" not in _code_strings(fns[name]), (
            f"{name} substitutes the literal measure again")
    for node in tree.body:
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            assert not any(
                isinstance(c, ast.Constant) and c.value == "period_net_amount"
                for c in ast.walk(node)
            ), "a module-level constant holds the old default measure"


def test_both_read_paths_fill_the_measure_through_the_one_helper():
    """The fill must reach the query dict, not just the validator: a measure
    validated but not propagated dies at the _SAFE_IDENTIFIER check."""
    content = _content()
    assert "_measure_for" in _funcs()
    value_block = content.split("def epm_value")[1].split("\n@frappe.whitelist")[0]
    batch_block = content.split("def epm_batch")[1].split("\n@frappe.whitelist")[0]
    assert "_measure_for(" in value_block
    assert "_measure_for(" in batch_block


def test_a_dataset_row_missing_the_column_is_refused_by_name():
    """What actually happens if default_measure were dropped from _FACT_FIELDS.

    A Dataset row arrives as frappe._dict (``__getattr__ = dict.get``), so an
    unselected column reads as None rather than raising: the read is refused
    with "declares no Default Measure", never queried with a blank. The pin on
    _FACT_FIELDS above is what keeps that from happening; this says what the
    failure would look like, and that nothing guesses a measure in its place.
    """
    api = _load_api()
    row = _FrappeRow(
        fact_name="zz_headcount",
        measures=json.dumps(["driver_value", "zz_fte"]),
        dimensions="[]",
    )
    assert row.default_measure is None  # the column was never selected

    assert api._measure_for(row, "") is None
    assert api._measure_for(row, "zz_fte") == "zz_fte"

    api._get_fact = lambda fact=None, scenario=None: row
    api._published_measures = lambda: {"driver_value", "zz_fte"}
    fact_doc, err = api._resolve_and_validate("zz_headcount", "zz", "", [])
    assert fact_doc is None
    assert "declares no Default Measure" in err, err
    assert "zz_headcount" in err, err


def test_resolve_and_validate_still_returns_a_pair():
    """Three test files stub this helper as a 4-arg callable returning a pair;
    a 3-tuple would break every one of them."""
    fn = next(n for n in ast.walk(ast.parse(_content()))
              if isinstance(n, ast.FunctionDef) and n.name == "_resolve_and_validate")
    assert [a.arg for a in fn.args.args] == [
        "fact_name", "scenario", "measure", "dim_names"]
    for node in ast.walk(fn):
        if isinstance(node, ast.Return):
            assert isinstance(node.value, ast.Tuple), ast.dump(node)
            assert len(node.value.elts) == 2, ast.dump(node)


# -- exercised, not only read: api.py on a stub frappe ----------------------


class _Invalid(Exception):
    pass


def _load_api():
    """api.py under a private name on a stub frappe, so the read path can be
    run on any host."""
    fake = types.ModuleType("frappe")
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
    stubs = {"frappe": fake, "frappe.utils": utils, "konsol.clickhouse": ch}
    before = set(sys.modules)
    saved = {k: sys.modules.get(k) for k in stubs}
    sys.modules.update(stubs)
    try:
        spec = importlib.util.spec_from_file_location("_host_api_k105", API_PATH)
        api = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(api)
        except ImportError as e:
            # A read-path pin must fail, not be counted as "needs frappe".
            raise AssertionError(f"api.py needs more than the stubs at import: {e}")
    finally:
        for key in set(sys.modules) - before:
            if key.split(".")[0] in ("frappe", "konsol"):
                del sys.modules[key]
        for k, mod in saved.items():
            if mod is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = mod
    return api


class _FrappeRow(dict):
    """A row as frappe.get_all returns it: frappe._dict, whose ``__getattr__``
    is ``dict.get`` (frappe/types/frappedict.py), so an unselected column reads
    as None instead of raising."""

    __getattr__ = dict.get


def _dataset(fact_name, default_measure, measures):
    """A Dataset doc as _FACT_FIELDS loads it."""
    return types.SimpleNamespace(
        fact_name=fact_name,
        default_measure=default_measure,
        measures=json.dumps(list(measures)),
        dimensions="[]",
        clickhouse_table="epm_gold.zz_table",
        scenario_key="zz",
        has_scenario_id=0,
        has_layer=0,
        reroute_table=None,
        reroute_column=None,
        reroute_measure=None,
    )


_ZZ_DRIVER = _dataset("zz_headcount", "driver_value", ["driver_value", "zz_fte"])
_ZZ_NO_DEFAULT = _dataset("zz_cashflow", "", ["cash_flow_amount", "zz_cash_in"])


def _api_for(dataset):
    api = _load_api()
    api._get_fact = lambda fact=None, scenario=None: dataset
    api._get_fact_by_scenario = lambda scenario: dataset
    api._published_measures = lambda: set(json.loads(dataset.measures))
    return api


def _resolve(dataset, measure):
    """(fact_doc, error, the measure a query would then be built with)."""
    api = _api_for(dataset)
    fact_doc, err = api._resolve_and_validate(dataset.fact_name, "zz", measure, [])
    filled = api._measure_for(fact_doc, measure) if fact_doc else None
    return fact_doc, err, filled


def test_a_blank_measure_resolves_to_the_datasets_default():
    fact_doc, err, filled = _resolve(_ZZ_DRIVER, "")
    assert err is None, err
    assert filled == "driver_value"


def test_a_blank_measure_on_a_dataset_with_no_default_names_it():
    fact_doc, err, _ = _resolve(_ZZ_NO_DEFAULT, "")
    assert fact_doc is None
    assert "zz_cashflow" in err, err
    assert "cash_flow_amount" in err and "zz_cash_in" in err, err
    # no guess, no first measure, and never the old literal
    assert "period_net_amount" not in err


def test_a_named_measure_is_not_overridden_by_the_default():
    fact_doc, err, filled = _resolve(_ZZ_DRIVER, "zz_fte")
    assert err is None, err
    assert filled == "zz_fte"


def test_an_invalid_named_measure_still_errors():
    fact_doc, err, _ = _resolve(_ZZ_DRIVER, "zz_nonsense")
    assert fact_doc is None
    assert "Invalid measure 'zz_nonsense'" in err, err


# -- the fill reaches the query, on both call sites -------------------------


def _stub_modules(fn):
    """Run ``fn`` with the modules the endpoints import inside their bodies."""
    perms = types.ModuleType("konsol.entity_permissions")
    perms.entity_read_scope = lambda entity, allowed, wildcard=False: (None, None)
    hq = types.ModuleType("konsol.hierarchy_query")
    hq.batch_query_hierarchy = lambda reqs, **kw: {"values": [0.0] * len(reqs)}
    hq.entity_is_wildcard = lambda entity: False
    hq.validate_hierarchy_read = lambda name, node, scenario: (
        {"hierarchy_name": "H", "member_code": node}, None)
    stubs = {"konsol.entity_permissions": perms, "konsol.hierarchy_query": hq}
    saved = {k: sys.modules.get(k) for k in stubs}
    sys.modules.update(stubs)
    try:
        return fn()
    finally:
        for k, mod in saved.items():
            if mod is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = mod


def _recorded(api):
    """Replace the flat query and collect the request dicts it is handed."""
    seen = []

    def flat_query(reqs):
        seen.extend(reqs)
        return {"values": [1.0] * len(reqs)}

    api._batch_query_clickhouse = flat_query
    return seen


def test_epm_value_queries_the_filled_measure():
    """A blank measure must reach the query as the Dataset's default — a fill
    that only validates would query "" and fail the identifier check."""
    api = _api_for(_ZZ_DRIVER)
    api._assert_entity_access = lambda entity: None
    seen = _recorded(api)
    _stub_modules(lambda: api.epm_value(
        "ZZ01", 2024, "FY", "ZZ4010", fact="zz_headcount", scenario="zz"))
    assert seen[0]["measure"] == "driver_value"


def test_epm_batch_queries_the_filled_measure():
    api = _api_for(_ZZ_DRIVER)
    api._allowed_entities = lambda: None
    api._get_json_body = lambda: [{
        "entity": "ZZ01", "year": 2026, "period": 1, "account": "ZZ4010",
        "fact": "zz_headcount", "scenario": "zz",
    }]
    seen = _recorded(api)
    result = _stub_modules(api.epm_batch)
    assert not (result.get("errors") or [None])[0], result
    assert seen[0]["measure"] == "driver_value"

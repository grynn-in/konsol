"""Structural and unit tests for hierarchy_query (EPM v2)."""
import ast
import json
import os

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read(rel):
    with open(os.path.join(APP_DIR, rel)) as f:
        return f.read()


def test_hierarchy_query_module_exports():
    from konsol.hierarchy_query import (
        HIERARCHY_SCENARIO_CONFIG,
        batch_query_hierarchy,
        entity_is_wildcard,
        resolve_hierarchy_name,
        validate_hierarchy_read,
        validate_hierarchy_write,
    )
    assert "actuals" in HIERARCHY_SCENARIO_CONFIG
    assert "budget" in HIERARCHY_SCENARIO_CONFIG
    assert "variance" in HIERARCHY_SCENARIO_CONFIG
    assert callable(batch_query_hierarchy)
    assert callable(resolve_hierarchy_name)
    assert callable(validate_hierarchy_read)
    assert callable(validate_hierarchy_write)


def test_entity_wildcard():
    from konsol.hierarchy_query import entity_is_wildcard
    assert entity_is_wildcard("")
    assert entity_is_wildcard("*")
    assert entity_is_wildcard("ALL")
    assert entity_is_wildcard("all")
    assert not entity_is_wildcard("USMF")


def test_batch_query_hierarchy_no_duplicate_query_blocks():
    src = _read("hierarchy_query.py")
    assert src.count("def batch_query_hierarchy") == 1
    assert ".format(" not in src.split("def batch_query_hierarchy")[1]
    assert "sql_fixed" not in src


def test_api_wires_hierarchy_mode():
    src = _read("api.py")
    for fn in [
        "_extract_batch_dimensions",
        "_is_hierarchy_mode",
        "batch_query_hierarchy",
        "validate_hierarchy_read",
        "validate_hierarchy_write",
    ]:
        assert fn in src, f"Missing {fn} in api.py"


def test_epm_batch_splits_hierarchy_and_legacy():
    block = _read("api.py").split("def epm_batch")[1].split("\n@frappe.whitelist")[0]
    assert "hierarchy_valid" in block
    assert "legacy_valid" in block
    assert "batch_query_hierarchy" in block
    assert "_batch_query_clickhouse" in block


def test_epm_value_accepts_hierarchy_params():
    sig = _read("api.py").split("def epm_value")[1].split("):")[0]
    assert "hierarchy" in sig
    assert "node" in sig


def test_budget_cell_save_validates_hierarchy_leaf():
    block = _read("api.py").split("def budget_cell_save")[1].split("\n@frappe.whitelist")[0]
    assert "validate_hierarchy_write" in block


def test_excel_functions_json_has_hierarchy_params():
    meta = json.load(open(os.path.join(APP_DIR, "public", "excel-addin", "functions.json")))
    epm = next(f for f in meta["functions"] if f["id"] == "EPM")
    names = [p["name"] for p in epm["parameters"]]
    assert "hierarchy" in names
    assert "node" in names


def test_excel_functions_js_passes_hierarchy_node():
    src = _read(os.path.join("public", "excel-addin", "functions.js"))
    assert "req.hierarchy" in src
    assert "req.hierarchy_node" in src
    assert "data.hierarchy_node" in src


def test_batch_query_groups_by_entity():
    """Non-wildcard batches must include entity in the group key, not a shared filter."""
    src = _read("hierarchy_query.py")
    batch = src.split("def batch_query_hierarchy")[1].split("\ndef ")[0]
    assert 'wildcard else req.get("entity", "")' in batch
    assert "group_items[0][1].get(" not in batch
    assert "param_entity" not in batch


def test_legacy_cost_center_mapped_in_api():
    src = _read("api.py")
    assert "_LEGACY_DIM_MAP" in src
    assert "dim_cost_center" in src.split("_LEGACY_DIM_MAP")[1].split("def _resolve_and_validate")[0]

# ── budget layer filter in hierarchy mode (grynn-in/konsol#63) ──────────

def test_budget_forecast_configs_are_layered():
    from konsol.hierarchy_query import HIERARCHY_SCENARIO_CONFIG as C
    # budget/forecast carry layered facts; actuals/variance do not
    assert C["budget"].get("has_layer") is True
    assert C["forecast"].get("has_layer") is True
    assert not C["actuals"].get("has_layer")
    assert not C["variance"].get("has_layer")


def test_hierarchy_query_gates_layer_filter():
    src = _read("hierarchy_query.py")
    # layer threaded through the group key
    assert 'req.get("layer", "")' in src
    # gated on the scenario config's has_layer, mirroring the flat path
    assert 'cfg.get("has_layer")' in src
    # applied as a parameterized clause (this exact clause is unique to the
    # hierarchy builder; the flat one lives in api.py)
    assert "AND layer = {layer:String}" in src


def test_api_hierarchy_requests_carry_layer():
    src = _read("api.py")
    # the epm_batch hierarchy-normalized dict (just before the _hierarchy_mode
    # marker) must carry layer into batch_query_hierarchy
    pre = src.split('"_hierarchy_mode": True')[0]
    assert '"layer"' in pre[-400:]
    # the epm_value hierarchy call passes the layer arg through
    assert '"layer": layer' in src


# ── variance reads one budget scenario (konsol#214) ─────────────────────────
#
# The warehouse variance model keeps one set of rows per active budget
# scenario (column budget_scenario_id). A read that does not filter on it adds
# budget scenarios together. hierarchy_query is run against a stub frappe
# (the Scenario doctype) and a fake ClickHouse that records the SQL.

import importlib.util  # noqa: E402
import sys  # noqa: E402
import types  # noqa: E402

_NO_BUDGET = "No active budget scenario belongs to FY2026, so there is no variance to show."


def _run_hierarchy(rows, active_budgets=(), cycles=None, scenarios=None, api=None):
    """batch_query_hierarchy over ``rows`` (dicts overriding a default request).

    ``active_budgets`` is what the Scenario doctype holds as active budget
    scenarios; ``cycles`` the (scenario_id, fiscal_year) of the Budget Cycles
    (default: one FY2026 cycle per active budget, the default request year).
    ``scenarios``, when given, is the Scenario records instead, as
    (scenario_id, scenario_type, is_active), and a lookup applies its filters.
    Returns (result, [(sql, params), ...], [Scenario get_all filters, ...]);
    Budget Cycle lookups are recorded as {"Budget Cycle": filters}.
    ``api``, when given, is an api module (see ``_failing_api``) served as
    konsol.api, and ClickHouse is queried through it instead of the fake;
    hierarchy_query's own log_error calls then go to ``api._zz_logged`` too.
    """
    queries, lookups = [], []
    if scenarios is not None:
        active_budgets = [s for s, kind, on in scenarios if kind == "budget" and on]
    if cycles is None:
        cycles = [(s, 2026) for s in active_budgets]

    def get_all(doctype, filters=None, pluck=None, order_by=None, fields=None, **kw):
        filters = dict(filters or {})
        if doctype == "Budget Cycle":
            lookups.append({"Budget Cycle": filters})
            wanted = set(filters["scenario_id"][1])
            return [{"scenario_id": s, "fiscal_year": y}
                    for s, y in cycles if s in wanted]
        assert doctype == "Scenario", doctype
        lookups.append(filters)
        if scenarios is None:
            return list(active_budgets)
        found = []
        for sid, kind, on in scenarios:
            record = {"scenario_id": sid, "scenario_type": kind, "is_active": on}
            if all(record[k] in v[1] if isinstance(v, list) else record[k] == v
                   for k, v in filters.items()):
                found.append(sid)
        return sorted(found)

    fake_frappe = types.ModuleType("frappe")
    fake_frappe.get_all = get_all
    if api is None:
        fake_frappe.log_error = lambda *a, **k: None
    else:
        fake_frappe.log_error = (
            lambda title=None, message=None, **k: api._zz_logged.append((title, message)))
    fake_frappe.get_traceback = lambda: "Traceback: hierarchy"

    spec = importlib.util.spec_from_file_location(
        "_host_hierarchy_query_k214", os.path.join(APP_DIR, "hierarchy_query.py"))
    hq = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(hq)

    def fake_query(sql, params, ch_settings):
        queries.append((sql, dict(params)))
        return ""

    ch_settings = {} if api is None else {"user": "zz", "password": "zz"}
    stubs = {
        "frappe": fake_frappe,
        "konsol.clickhouse": types.SimpleNamespace(get_connection=lambda: ch_settings),
        "konsol.entity_permissions": types.SimpleNamespace(
            entity_read_scope=lambda entity, allowed, wildcard=False: (None, None)),
    }
    if api is None:
        hq._clickhouse_query = fake_query
    else:
        stubs["konsol.api"] = api
    saved = {k: sys.modules.get(k) for k in stubs}
    sys.modules.update(stubs)
    try:
        reqs = []
        for i, row in enumerate(rows):
            req = {
                "entity": "ZZ01", "year": 2026, "periods": (1,), "account": f"ZZ{i}",
                "scenario": "variance", "hierarchy_name": "ZZ_H", "hierarchy_node": "ZZ_N",
                # A direct call must name a measure (konsol#105 Decision 1):
                # the API fills a blank one from the Dataset registry before
                # the request gets here, and this module refuses a blank.
                "measure": "variance_abs",
            }
            req.update(row)
            reqs.append(req)
        result = hq.batch_query_hierarchy(reqs, allowed_entities=None)
    finally:
        for k, mod in saved.items():
            if mod is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = mod
    return result, queries, lookups


def test_variance_filters_the_named_budget_scenario():
    result, queries, lookups = _run_hierarchy(
        [{"scenario_id": "ZZ_PLAN"}], active_budgets=["ZZ_PLAN"])
    assert not result.get("errors")
    (sql, params), = queries
    assert "AND budget_scenario_id = {sid:String}" in sql
    assert params["param_sid"] == "ZZ_PLAN"
    # a named scenario is read as named, once the doctype says it is an
    # active budget; its year's Budget Cycles are not asked
    assert lookups == [{"scenario_type": "budget", "is_active": 1}]


# ── a named scenario must be a budget the warehouse holds (PR #217 review 4)
#
# The warehouse variance models keep only active budget scenarios, so a named
# actuals, inactive or misspelled id would filter to nothing and read 0.0.

_ZZ_SCENARIOS = [
    ("ZZ_ACT", "actual", 1),
    ("ZZ_OLD", "budget", 0),
    ("ZZ_PLAN", "budget", 1),
]


def test_variance_with_a_named_actuals_scenario_is_refused():
    result, queries, _ = _run_hierarchy(
        [{"scenario_id": "ZZ_ACT"}], scenarios=_ZZ_SCENARIOS)
    assert queries == []
    assert result["errors"] == [
        "ZZ_ACT is not an active budget scenario, so there is no variance for it."]
    assert result["values"] == [None]


def test_variance_with_a_named_inactive_budget_is_refused():
    result, queries, _ = _run_hierarchy(
        [{"scenario_id": "ZZ_OLD"}], scenarios=_ZZ_SCENARIOS)
    assert queries == []
    assert result["errors"] == [
        "ZZ_OLD is not an active budget scenario, so there is no variance for it."]


def test_variance_with_an_unknown_named_scenario_is_refused():
    result, queries, _ = _run_hierarchy(
        [{"scenario_id": "ZZ_PLNA"}], scenarios=_ZZ_SCENARIOS)
    assert queries == []
    assert result["errors"] == [
        "ZZ_PLNA is not an active budget scenario, so there is no variance for it."]


def test_variance_with_a_named_active_budget_is_filtered_and_refusal_is_per_row():
    result, queries, lookups = _run_hierarchy(
        [{"scenario_id": "ZZ_PLAN"}, {"scenario_id": "ZZ_ACT", "account": "ZZ9"}],
        scenarios=_ZZ_SCENARIOS)
    (sql, params), = queries
    assert "AND budget_scenario_id = {sid:String}" in sql
    assert params["param_sid"] == "ZZ_PLAN"
    assert result["errors"] == [
        None, "ZZ_ACT is not an active budget scenario, so there is no variance for it."]
    # one Scenario lookup, however many named rows
    assert lookups == [{"scenario_type": "budget", "is_active": 1}]


def test_variance_rejects_an_unsafe_scenario_id():
    result, queries, _ = _run_hierarchy([{"scenario_id": "ZZ PLAN;"}])
    assert queries == []
    assert result["errors"] == ["Invalid scenario_id format"]


def test_variance_without_scenario_reads_the_one_active_budget():
    result, queries, lookups = _run_hierarchy([{}], active_budgets=["ZZ_B1"])
    assert not result.get("errors")
    (sql, params), = queries
    assert "AND budget_scenario_id = {sid:String}" in sql
    assert params["param_sid"] == "ZZ_B1"
    assert lookups[0] == {"scenario_type": "budget", "is_active": 1}


def test_variance_without_scenario_and_no_active_budget_is_refused():
    result, queries, _ = _run_hierarchy([{}], active_budgets=[])
    assert queries == []
    assert result["errors"] == [_NO_BUDGET]
    assert result["values"] == [None]


def test_variance_without_scenario_and_several_active_budgets_is_refused():
    result, queries, _ = _run_hierarchy([{}], active_budgets=["ZZ_B1", "ZZ_B2"])
    assert queries == []
    assert result["errors"] == [
        "Several active budget scenarios belong to FY2026 (ZZ_B1, ZZ_B2); choose one."]


def test_active_budget_scenarios_are_looked_up_once_per_call():
    result, queries, lookups = _run_hierarchy(
        [{}, {"account": "ZZ9", "periods": (2,)}, {"account": "ZZ8", "year": 2027}],
        active_budgets=["ZZ_B1", "ZZ_B2"],
        cycles=[("ZZ_B1", 2026), ("ZZ_B2", 2027)])
    assert not result.get("errors")
    assert len(queries) == 3
    assert sorted(p["param_sid"] for _, p in queries) == ["ZZ_B1", "ZZ_B1", "ZZ_B2"]
    # one Scenario and one Budget Cycle lookup, however many years are read
    assert len(lookups) == 2


# ── with no scenario named, the budget is the request year's (PR #217 review 3)
#
# Once two years' budgets are both active, "the single active budget" refuses
# every unnamed read. A budget scenario belongs to the years of its Budget
# Cycles; an unnamed read uses the one active budget of the request's year.

def test_variance_without_scenario_reads_the_request_years_budget():
    both = dict(active_budgets=["ZZ_B26", "ZZ_B27"],
                cycles=[("ZZ_B26", 2026), ("ZZ_B27", 2027)])
    result, queries, _ = _run_hierarchy([{"year": 2026}], **both)
    assert not result.get("errors"), result
    (sql, params), = queries
    assert "AND budget_scenario_id = {sid:String}" in sql
    assert params["param_sid"] == "ZZ_B26"
    result, queries, _ = _run_hierarchy([{"year": 2027}], **both)
    assert not result.get("errors"), result
    (_, params), = queries
    assert params["param_sid"] == "ZZ_B27"


def test_variance_without_a_budget_for_the_year_is_refused():
    result, queries, _ = _run_hierarchy(
        [{"year": 2028}], active_budgets=["ZZ_B26", "ZZ_B27"],
        cycles=[("ZZ_B26", 2026), ("ZZ_B27", 2027)])
    assert queries == []
    assert result["errors"] == [
        "No active budget scenario belongs to FY2028, so there is no variance to show."]


def test_variance_with_several_budgets_for_the_year_is_refused_by_name():
    result, queries, _ = _run_hierarchy(
        [{"year": 2026}], active_budgets=["ZZ_B26", "ZZ_B26X", "ZZ_B27"],
        cycles=[("ZZ_B26", 2026), ("ZZ_B26X", 2026), ("ZZ_B27", 2027)])
    assert queries == []
    assert result["errors"] == [
        "Several active budget scenarios belong to FY2026 (ZZ_B26, ZZ_B26X); choose one."]


def test_a_cancelled_budget_cycle_does_not_count():
    _, _, lookups = _run_hierarchy([{}], active_budgets=["ZZ_B1"])
    cycle_filters, = [lk["Budget Cycle"] for lk in lookups if "Budget Cycle" in lk]
    assert cycle_filters["scenario_id"] == ["in", ["ZZ_B1"]]
    assert cycle_filters["docstatus"] == ["<", 2]


def _load_hq():
    spec = importlib.util.spec_from_file_location(
        "_host_hierarchy_query_k214_pure", os.path.join(APP_DIR, "hierarchy_query.py"))
    hq = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(hq)
    return hq


def test_budget_and_forecast_still_filter_scenario_id():
    for sc in ("budget", "forecast"):
        # measure named per scenario: variance_abs is not a budget measure,
        # and a direct call carries its own measure (konsol#105).
        result, queries, lookups = _run_hierarchy(
            [{"scenario": sc, "scenario_id": "ZZ_PLAN", "measure": "period_amount"}],
            active_budgets=["ZZ_B1"])
        assert not result.get("errors")
        (sql, params), = queries
        assert "AND scenario_id = {sid:String}" in sql
        assert "budget_scenario_id" not in sql
        assert params["param_sid"] == "ZZ_PLAN"
        assert lookups == []
        # no scenario named: all of the table, as before
        result, queries, lookups = _run_hierarchy(
            [{"scenario": sc, "measure": "period_amount"}], active_budgets=[])
        assert not result.get("errors")
        (sql, params), = queries
        assert "scenario_id" not in sql
        assert "param_sid" not in params
        assert lookups == []


# ── the hierarchy path reports ClickHouse failures like the flat path
#    (PR #217 review 2, point 1)
#
# hierarchy_query queries ClickHouse through api._clickhouse_query, so an
# unknown column shows ClickHouse's code and exception name (row K7) and the
# log gets ClickHouse's reply, not a bare traceback.

_CH_BODY = (
    "Code: 47. DB::Exception: Unknown expression identifier 'budget_scenario_id' "
    "in scope SELECT fiscal_year FROM epm_gold.gold_variance_at_hierarchy_node. "
    "(UNKNOWN_IDENTIFIER)\n" + "\n".join(f"{i}. DB::frame_{i}" for i in range(50))
)


class _CHResp:
    def __init__(self, status_code, text):
        self.status_code = status_code
        self.text = text

    def raise_for_status(self):
        import requests
        if self.status_code >= 400:
            raise requests.exceptions.HTTPError(
                f"{self.status_code} Client Error", response=self)


class _Invalid(Exception):
    """Stands in for frappe.ValidationError, which frappe.throw raises."""


def _load_api(get_all=None, log_error=None):
    """api.py under a private name on a stub frappe, so its read paths run on
    any host. ``get_all`` answers the Dataset registry lookups; ``log_error``
    records what the module logs."""
    fake_frappe = types.ModuleType("frappe")
    fake_frappe.get_all = get_all or (lambda *a, **k: [])
    fake_frappe.whitelist = lambda *a, **k: (lambda fn: fn)
    fake_frappe.log_error = log_error or (lambda *a, **k: None)
    fake_frappe.get_traceback = lambda: "Traceback: api"
    fake_frappe.ValidationError = _Invalid
    fake_frappe.PermissionError = _Invalid

    def throw(msg, exc=Exception, *a, **k):
        raise exc(msg)

    fake_frappe.throw = throw
    fake_utils = types.ModuleType("frappe.utils")
    fake_utils.now_datetime = lambda: None
    fake_frappe.utils = fake_utils
    stubs = {
        "frappe": fake_frappe,
        "frappe.utils": fake_utils,
        "konsol.clickhouse": types.SimpleNamespace(
            connection_url=lambda s: "http://zz-clickhouse:8123/",
            get_connection=lambda: {}),
    }
    before = set(sys.modules)
    saved = {k: sys.modules.get(k) for k in stubs}
    sys.modules.update(stubs)
    try:
        spec = importlib.util.spec_from_file_location(
            "_host_api_k214_hierarchy", os.path.join(APP_DIR, "api.py"))
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


def _failing_api(body):
    """api.py under a private name, a stub frappe recording log_error in
    ``api._zz_logged``, and a requests.post that answers HTTP 400 ``body``."""
    import requests
    logged = []
    api = _load_api(
        log_error=lambda title=None, message=None, **k: logged.append((title, message)))
    api.requests = types.SimpleNamespace(
        post=lambda *a, **k: _CHResp(400, body), exceptions=requests.exceptions)
    api._zz_logged = logged
    return api


def test_hierarchy_clickhouse_failure_shows_code_and_exception_name():
    api = _failing_api(_CH_BODY)
    result, _, _ = _run_hierarchy(
        [{"scenario_id": "ZZ_PLAN"}], active_budgets=["ZZ_PLAN"], api=api)
    assert result["values"] == [None]
    (err,) = result["errors"]
    assert err == "ClickHouse query failed (47: UNKNOWN_IDENTIFIER)"
    assert "budget_scenario_id" not in err
    # ClickHouse's reply is logged once, not once more as a bare traceback
    assert api._zz_logged == [("ClickHouse query failed", _CH_BODY[:1000])]


# ── the measure comes from the Dataset registry (konsol#105 Decision 1) ──────
#
# A fact's default measure lives on its Dataset. The API resolves it and fills
# a hierarchy request before handing it over; this module is a query builder
# that requires a measure and refuses a blank one. It must reach for neither
# konsol.api nor the registry: that import would make it need frappe, and a
# test file that stops importing is counted as skipped, not failed
# (konsol#248) — a whole file can leave the run while it still reads green.


def test_hierarchy_scenario_config_declares_no_default_measure():
    """The per-scenario guess is gone; the registry is the one source."""
    from konsol.hierarchy_query import HIERARCHY_SCENARIO_CONFIG as C
    carrying = sorted(sc for sc, cfg in C.items() if "default_measure" in cfg)
    assert carrying == [], f"still guessing a measure for: {carrying}"
    # asserted over the source too, so the key cannot creep back on a new entry
    assert "default_measure" not in _read("hierarchy_query.py")


def test_the_measure_path_reaches_for_neither_the_api_nor_the_registry():
    """Resolving the measure must not reach back into konsol.api: that import
    is what made this file stop importing on a host with no frappe, and the
    runner counts a file that stops importing as skipped, not failed
    (konsol#248) — the run stays green with the tests gone.

    konsol.api is blocked here, so a reach for it raises rather than hides. It
    stays importable in production for the ClickHouse execution path (the only
    thing that imports it), which this harness replaces with a fake.
    """
    saved = sys.modules.get("konsol.api")
    sys.modules["konsol.api"] = None  # any `import konsol.api` now raises
    try:
        # a blank measure is refused without consulting anything
        result, queries, _ = _run_hierarchy(
            [{"measure": ""}], active_budgets=["ZZ_B1"])
        assert queries == []
        assert "must name a measure" in (result["errors"][0] or ""), result
        # and a request that carries one is read as before (a clean result
        # carries no errors key at all)
        result, queries, _ = _run_hierarchy([{}], active_budgets=["ZZ_B1"])
        assert not result.get("errors"), result
        assert len(queries) == 1
    finally:
        if saved is None:
            sys.modules.pop("konsol.api", None)
        else:
            sys.modules["konsol.api"] = saved


def test_a_hierarchy_request_with_no_measure_is_refused_before_any_query():
    """A direct caller must name a measure: this module has nothing to fill it
    from, and must not guess one."""
    for blank in ("", None):
        result, queries, _ = _run_hierarchy(
            [{"measure": blank}], active_budgets=["ZZ_B1"])
        assert queries == []
        assert result["values"] == [None]
        (err,) = result["errors"]
        assert "must name a measure" in err, err
        assert "variance" in err and "Dataset" in err, err


# -- the API fills it, exercised on a stub frappe ----------------------------


def _dataset(scenario_key, default_measure):
    """A Dataset doc as _FACT_FIELDS loads it."""
    return types.SimpleNamespace(
        fact_name=f"zz_{scenario_key}",
        scenario_key=scenario_key,
        default_measure=default_measure,
        measures="[]",
        dimensions="[]",
        clickhouse_table="epm_gold.zz_table",
        has_scenario_id=0,
        has_layer=0,
        reroute_table=None,
        reroute_column=None,
        reroute_measure=None,
    )


def _hierarchy_api(registry):
    """(api, the requests it hands the query layer) for hierarchy reads.

    ``registry`` maps scenario_key → the Dataset's default_measure. A scenario
    absent from it has no Dataset at all, as forecast has none (konsol#106);
    a blank value is a Dataset that declares no default. Those are the two
    ways the registry can answer nothing, and neither is a licence to guess.
    """
    def get_all(doctype, filters=None, **kw):
        assert doctype == "Dataset", doctype
        key = (filters or {}).get("scenario_key")
        return [_dataset(key, registry[key])] if key in registry else []

    api = _load_api(get_all=get_all)
    api._allowed_entities = lambda: None
    seen = []

    def record(reqs, *, allowed_entities=None):
        seen.extend(reqs)
        return {"values": [0.0] * len(reqs), "errors": [None] * len(reqs)}

    hq = types.ModuleType("konsol.hierarchy_query")
    hq.batch_query_hierarchy = record
    hq.entity_is_wildcard = lambda entity: entity in ("", "*", "ALL")
    hq.validate_hierarchy_read = lambda name, node, scenario: (
        {"hierarchy_name": "ZZ_H", "member_code": node}, None)
    # The API normalises the scenario through this module before looking the
    # Dataset registry up (konsol#105 review), so the stub has to carry the
    # function too. The real one, imported here while the stub is not yet in
    # sys.modules, so it cannot drift from the rule the query layer applies.
    from konsol.hierarchy_query import _normalize_scenario
    hq._normalize_scenario = _normalize_scenario
    perms = types.ModuleType("konsol.entity_permissions")
    perms.entity_read_scope = lambda entity, allowed, wildcard=False: (None, None)
    api._zz_stubs = {"konsol.hierarchy_query": hq, "konsol.entity_permissions": perms}
    return api, seen


def _run_api(api, fn, *a, **k):
    """Call an endpoint with the modules it imports inside its body stubbed."""
    saved = {key: sys.modules.get(key) for key in api._zz_stubs}
    sys.modules.update(api._zz_stubs)
    try:
        return fn(*a, **k)
    except ImportError as e:
        # The runner counts a ModuleNotFoundError raised in a test body as a
        # skip and still exits 0 (konsol#248): fail here instead.
        raise AssertionError(f"{fn.__name__} needs more than the stubs: {e}") from e
    finally:
        for key, mod in saved.items():
            if mod is None:
                sys.modules.pop(key, None)
            else:
                sys.modules[key] = mod


def test_default_measure_for_scenario_answers_the_registry():
    api, _ = _hierarchy_api({"actuals": "period_debit", "zz_undeclared": ""})
    assert api.default_measure_for_scenario("actuals") == "period_debit"
    # the two ways the registry answers nothing: a Dataset declaring no
    # default, and no Dataset for the scenario at all
    assert api.default_measure_for_scenario("zz_undeclared") == ""
    assert api.default_measure_for_scenario("forecast") == ""


def test_a_blank_measure_on_a_hierarchy_read_takes_the_datasets_default():
    """The value comes from the Dataset, not from a table in the query layer:
    this registry declares period_debit, which no scenario config ever did."""
    api, seen = _hierarchy_api({"actuals": "period_debit"})
    _run_api(api, api.epm_value, "ZZ01", 2026, "FY", "ZZ4010", node="ZZ_N")
    assert [r["measure"] for r in seen] == ["period_debit"]


def test_a_named_measure_on_a_hierarchy_read_is_not_overridden():
    api, seen = _hierarchy_api({"actuals": "period_debit"})
    _run_api(api, api.epm_value, "ZZ01", 2026, "FY", "ZZ4010",
             measure="period_credit", node="ZZ_N")
    assert [r["measure"] for r in seen] == ["period_credit"]


def test_a_hierarchy_read_of_a_scenario_with_no_dataset_is_refused():
    """Deliberate, and Deepak's to change: forecast has no Dataset row, and
    whether it is a scenario of its own is konsol#106, still open. A forecast
    hierarchy read naming no measure ends in an error that names the scenario
    — it must not fall back to a budget measure."""
    api, seen = _hierarchy_api({"actuals": "period_debit"})
    try:
        _run_api(api, api.epm_value, "ZZ01", 2026, "FY", "ZZ4010",
                 scenario="forecast", node="ZZ_N")
    except _Invalid as e:
        err = str(e)
    else:
        raise AssertionError("a forecast hierarchy read with no measure was not refused")
    assert seen == [], "the read reached the query layer"
    assert "forecast" in err, err
    # Both causes, offered as alternatives: a flat "no Dataset is registered"
    # would be false for a scenario that has one which declares no default.
    assert "either no Dataset is registered" in err, err
    assert "declares no Default Measure" in err, err
    assert "period_amount" not in err, err


def test_a_hierarchy_read_of_a_dataset_declaring_no_default_is_refused():
    api, seen = _hierarchy_api({"actuals": ""})
    try:
        _run_api(api, api.epm_value, "ZZ01", 2026, "FY", "ZZ4010", node="ZZ_N")
    except _Invalid as e:
        err = str(e)
    else:
        raise AssertionError("a hierarchy read with no measure to fill was not refused")
    assert seen == [], "the read reached the query layer"
    assert "actuals" in err, err


def test_epm_batch_fills_hierarchy_rows_and_refuses_one_row_at_a_time():
    api, seen = _hierarchy_api({"actuals": "period_debit"})
    api._get_json_body = lambda: [
        {"entity": "ZZ01", "year": 2026, "period": 1, "account": "ZZ4010",
         "hierarchy_node": "ZZ_N"},
        {"entity": "ZZ01", "year": 2026, "period": 1, "account": "ZZ4011",
         "hierarchy_node": "ZZ_N", "scenario": "forecast"},
    ]
    result = _run_api(api, api.epm_batch)
    # the readable row is queried with the Dataset's default; the other is not
    # queried at all, and says why
    assert [r["measure"] for r in seen] == ["period_debit"]
    first, second = result["errors"]
    assert first is None, first
    assert "forecast" in second, second
    assert result["values"][1] is None


# ── no add-in tooltip may name a default measure (PR #249 review) ───────────
#
# functions.json is the tooltip Excel shows in the formula bar: the most
# user-visible text in the product. Since a fact's default measure lives on
# its Dataset (konsol#105 Decision 1), no tooltip can name one — of the nine
# shipped datasets only two declare period_net_amount, so the old wording
# "Measure (default period_net_amount)" was wrong for the other seven. The
# api.py tripwire only ever read api.py, which is how that line survived the
# branch. These assert over the shipped fixtures rather than a literal, so
# they answer for whatever set of datasets a customer ships.


def _addin_functions():
    with open(os.path.join(APP_DIR, "public", "excel-addin", "functions.json")) as f:
        return json.load(f)["functions"]


def _declared_default_measures():
    """Every measure some shipped Dataset declares as its default."""
    from konsol.tests.shipped import shipped  # konsol#230: now in defaults/
    datasets = shipped("dataset.json")
    return sorted({d["default_measure"] for d in datasets if d.get("default_measure")})


def test_no_addin_parameter_tooltip_claims_a_hardcoded_default_measure():
    declared = _declared_default_measures()
    # the premise: the datasets disagree, so no one measure is "the" default
    assert len(declared) > 1, declared
    for fn in _addin_functions():
        for param in fn["parameters"]:
            desc = param["description"]
            # the measure tooltip may not name one at all; any other tooltip
            # may not name one as a default
            if param["name"] != "measure" and "default" not in desc.lower():
                continue
            named = [m for m in declared if m in desc]
            assert not named, (
                f"{fn['id']} tooltip for '{param['name']}' names {named} as the "
                f"default measure, but each Dataset declares its own "
                f"(konsol#105 Decision 1): {desc!r}"
            )


def test_the_measure_tooltip_points_at_the_dataset():
    """Not the old sentence re-pinned: a blank measure has to resolve
    somewhere, and the tooltip has to say where."""
    (epm,) = [f for f in _addin_functions() if f["id"] == "EPM"]
    (measure,) = [p for p in epm["parameters"] if p["name"] == "measure"]
    assert "dataset" in measure["description"].lower(), measure["description"]

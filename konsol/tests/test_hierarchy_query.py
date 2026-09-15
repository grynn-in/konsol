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


def _run_hierarchy(rows, active_budgets=(), cycles=None, scenarios=None):
    """batch_query_hierarchy over ``rows`` (dicts overriding a default request).

    ``active_budgets`` is what the Scenario doctype holds as active budget
    scenarios; ``cycles`` the (scenario_id, fiscal_year) of the Budget Cycles
    (default: one FY2026 cycle per active budget, the default request year).
    ``scenarios``, when given, is the Scenario records instead, as
    (scenario_id, scenario_type, is_active), and a lookup applies its filters.
    Returns (result, [(sql, params), ...], [Scenario get_all filters, ...]);
    Budget Cycle lookups are recorded as {"Budget Cycle": filters}.
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
    fake_frappe.log_error = lambda *a, **k: None
    fake_frappe.get_traceback = lambda: ""

    spec = importlib.util.spec_from_file_location(
        "_host_hierarchy_query_k214", os.path.join(APP_DIR, "hierarchy_query.py"))
    hq = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(hq)

    def fake_query(sql, params, ch_settings):
        queries.append((sql, dict(params)))
        return ""

    hq._clickhouse_query = fake_query
    stubs = {
        "frappe": fake_frappe,
        "konsol.clickhouse": types.SimpleNamespace(get_connection=lambda: {}),
        "konsol.entity_permissions": types.SimpleNamespace(
            entity_read_scope=lambda entity, allowed, wildcard=False: (None, None)),
    }
    saved = {k: sys.modules.get(k) for k in stubs}
    sys.modules.update(stubs)
    try:
        reqs = []
        for i, row in enumerate(rows):
            req = {
                "entity": "ZZ01", "year": 2026, "periods": (1,), "account": f"ZZ{i}",
                "scenario": "variance", "hierarchy_name": "ZZ_H", "hierarchy_node": "ZZ_N",
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
        result, queries, lookups = _run_hierarchy(
            [{"scenario": sc, "scenario_id": "ZZ_PLAN"}], active_budgets=["ZZ_B1"])
        assert not result.get("errors")
        (sql, params), = queries
        assert "AND scenario_id = {sid:String}" in sql
        assert "budget_scenario_id" not in sql
        assert params["param_sid"] == "ZZ_PLAN"
        assert lookups == []
        # no scenario named: all of the table, as before
        result, queries, lookups = _run_hierarchy([{"scenario": sc}], active_budgets=[])
        assert not result.get("errors")
        (sql, params), = queries
        assert "scenario_id" not in sql
        assert "param_sid" not in params
        assert lookups == []

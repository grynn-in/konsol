"""The variance_analysis Dataset reads one budget scenario (konsol#214).

The warehouse variance model keeps one set of rows per active budget scenario
(column budget_scenario_id). A flat Dataset read (api._batch_query_clickhouse)
that does not filter on it adds budget scenarios together. api.py is loaded
under a private name with a stub frappe (the Scenario doctype) and a fake
ClickHouse that records the SQL; the Dataset registry is replaced by fixed
records.
"""
import importlib.util
import os
import sys
import types

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_NO_BUDGET = "No budget scenario is active, so there is no variance to show."


def _fact(fact_name, scenario_key, table, has_scenario_id):
    return types.SimpleNamespace(
        fact_name=fact_name, scenario_key=scenario_key, clickhouse_table=table,
        has_scenario_id=has_scenario_id, has_layer=0,
        reroute_table=None, reroute_column=None, reroute_measure=None,
    )


_FACTS = [
    _fact("variance_analysis", "variance", "epm_gold.gold_variance_analysis", 0),
    _fact("zz_budget", "budget", "epm_gold.zz_budget", 1),
    _fact("zz_actuals", "actuals", "epm_gold.zz_actuals", 0),
]


def _run(rows, active_budgets=()):
    """api._batch_query_clickhouse over ``rows`` (dicts overriding a default
    variance request). ``active_budgets`` is what the Scenario doctype holds
    as active budget scenarios.

    Returns (result, [(sql, params), ...], [Scenario get_all filters, ...]).
    """
    queries, lookups = [], []

    def get_all(doctype, filters=None, **kw):
        assert doctype == "Scenario", doctype
        lookups.append(dict(filters or {}))
        return list(active_budgets)

    fake_frappe = types.ModuleType("frappe")
    fake_frappe.get_all = get_all
    fake_frappe.whitelist = lambda *a, **k: (lambda fn: fn)
    fake_frappe.log_error = lambda *a, **k: None
    fake_frappe.get_traceback = lambda: ""
    fake_utils = types.ModuleType("frappe.utils")
    fake_utils.now_datetime = lambda: None
    fake_frappe.utils = fake_utils
    stubs = {
        "frappe": fake_frappe,
        "frappe.utils": fake_utils,
        "konsol.clickhouse": types.SimpleNamespace(
            connection_url=lambda s: "", get_connection=lambda: {}),
    }

    before = set(sys.modules)
    saved = {k: sys.modules.get(k) for k in stubs}
    sys.modules.update(stubs)
    try:
        spec = importlib.util.spec_from_file_location(
            "_host_api_k214", os.path.join(APP_DIR, "api.py"))
        api = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(api)

        by_name = {f.fact_name: f for f in _FACTS}
        by_key = {f.scenario_key: f for f in _FACTS}
        api._get_fact = lambda fact=None, scenario=None: by_name.get(fact)
        api._get_fact_by_scenario = lambda scenario: by_key.get(scenario)
        api._get_ch_connection = lambda: {}

        def fake_query(sql, params, ch_settings):
            queries.append((sql, dict(params)))
            return ""

        api._clickhouse_query = fake_query

        reqs = []
        for i, row in enumerate(rows):
            req = {
                "entity": "ZZ01", "year": 2026, "account": f"ZZ{i}",
                "measure": "variance_amount", "periods": (1,), "dimensions": {},
                "fact": "variance_analysis",
            }
            req.update(row)
            reqs.append(req)
        result = api._batch_query_clickhouse(reqs)
    finally:
        for key in set(sys.modules) - before:
            if key.split(".")[0] in ("frappe", "konsol"):
                del sys.modules[key]
        for k, mod in saved.items():
            if mod is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = mod
    return result, queries, lookups


def test_variance_dataset_filters_the_named_budget_scenario():
    result, queries, lookups = _run([{"scenario_id": "ZZ_PLAN"}])
    assert not result.get("errors")
    (sql, params), = queries
    assert "FROM epm_gold.gold_variance_analysis" in sql
    assert "AND budget_scenario_id = {sid:String}" in sql
    assert params["param_sid"] == "ZZ_PLAN"
    # a named scenario is read as named; the doctype is not asked
    assert lookups == []


def test_variance_dataset_rejects_an_unsafe_scenario_id():
    result, queries, _ = _run([{"scenario_id": "ZZ PLAN;"}])
    assert queries == []
    assert result["errors"] == ["Invalid scenario_id format"]


def test_variance_dataset_without_scenario_reads_the_one_active_budget():
    result, queries, lookups = _run([{}], active_budgets=["ZZ_B1"])
    assert not result.get("errors")
    (sql, params), = queries
    assert "AND budget_scenario_id = {sid:String}" in sql
    assert params["param_sid"] == "ZZ_B1"
    assert lookups == [{"scenario_type": "budget", "is_active": 1}]


def test_variance_dataset_by_scenario_key_reads_the_one_active_budget():
    result, queries, _ = _run(
        [{"fact": None, "scenario": "variance"}], active_budgets=["ZZ_B1"])
    assert not result.get("errors")
    (sql, params), = queries
    assert "AND budget_scenario_id = {sid:String}" in sql
    assert params["param_sid"] == "ZZ_B1"


def test_variance_dataset_without_scenario_and_no_active_budget_is_refused():
    result, queries, _ = _run([{}], active_budgets=[])
    assert queries == []
    assert result["errors"] == [_NO_BUDGET]


def test_variance_dataset_with_several_active_budgets_is_refused_by_name():
    result, queries, _ = _run([{}], active_budgets=["ZZ_B1", "ZZ_B2"])
    assert queries == []
    assert result["errors"] == [
        "Several budget scenarios are active (ZZ_B1, ZZ_B2); choose one."]


def test_active_budgets_are_looked_up_once_per_call():
    result, queries, lookups = _run(
        [{}, {"measure": "actual_amount"}], active_budgets=["ZZ_B1"])
    assert not result.get("errors")
    assert len(queries) == 2
    assert all(p["param_sid"] == "ZZ_B1" for _, p in queries)
    assert len(lookups) == 1


def test_other_datasets_are_unchanged():
    result, queries, lookups = _run([
        {"fact": "zz_budget", "measure": "budget_amount", "scenario_id": "ZZ_PLAN"},
        {"fact": "zz_actuals", "measure": "period_net_amount"},
    ])
    assert not result.get("errors")
    assert len(queries) == 2
    budget_sql, budget_params = next(q for q in queries if "zz_budget" in q[0])
    assert "AND scenario_id = {sid:String}" in budget_sql
    assert "budget_scenario_id" not in budget_sql
    assert budget_params["param_sid"] == "ZZ_PLAN"
    actuals_sql, actuals_params = next(q for q in queries if "zz_actuals" in q[0])
    assert "scenario_id" not in actuals_sql
    assert "param_sid" not in actuals_params
    # no variance read, so the Scenario doctype is never asked
    assert lookups == []

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

_NO_BUDGET = "No active budget scenario belongs to FY2026, so there is no variance to show."


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


class _ValidationError(Exception):
    pass


def _throw(msg, exc=_ValidationError, **kw):
    raise exc(msg)


def _run(rows, active_budgets=(), call=None, reply="", cycles=None, scenarios=None):
    """api._batch_query_clickhouse over ``rows`` (dicts overriding a default
    variance request). ``active_budgets`` is what the Scenario doctype holds
    as active budget scenarios; ``cycles`` the (scenario_id, fiscal_year) of
    the Budget Cycles (default: one FY2026 cycle per active budget, the
    default request year). ``scenarios``, when given, is the Scenario records
    instead, as (scenario_id, scenario_type, is_active), and a lookup applies
    its filters. ``call(api)``, when given, replaces the batch read (its
    return value is the result); ``reply`` is ClickHouse's answer.

    Returns (result, [(sql, params), ...], [Scenario get_all filters, ...]);
    Budget Cycle lookups are recorded as {"Budget Cycle": filters}.
    """
    queries, lookups = [], []
    if scenarios is not None:
        active_budgets = [s for s, kind, on in scenarios if kind == "budget" and on]
    if cycles is None:
        cycles = [(s, 2026) for s in active_budgets]

    def get_all(doctype, filters=None, **kw):
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
    fake_frappe.whitelist = lambda *a, **k: (lambda fn: fn)
    fake_frappe.log_error = lambda *a, **k: None
    fake_frappe.get_traceback = lambda: ""
    fake_frappe.throw = _throw
    fake_frappe.ValidationError = _ValidationError
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
            return reply

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
        if call is not None:
            result = call(api)
        else:
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
    result, queries, lookups = _run(
        [{"scenario_id": "ZZ_PLAN"}], active_budgets=["ZZ_PLAN"])
    assert not result.get("errors")
    (sql, params), = queries
    assert "FROM epm_gold.gold_variance_analysis" in sql
    assert "AND budget_scenario_id = {sid:String}" in sql
    assert params["param_sid"] == "ZZ_PLAN"
    # a named scenario is read as named, once the doctype says it is an
    # active budget; its year's Budget Cycles are not asked
    assert lookups == [{"scenario_type": "budget", "is_active": 1}]


def test_variance_dataset_rejects_an_unsafe_scenario_id():
    result, queries, _ = _run([{"scenario_id": "ZZ PLAN;"}])
    assert queries == []
    assert result["errors"] == ["Invalid scenario_id format"]


# -- a named scenario must be a budget the warehouse holds (PR #217 review 4)
#
# The warehouse variance model keeps only active budget scenarios, so a named
# actuals, inactive or misspelled id would filter to nothing and read 0.0.

_ZZ_SCENARIOS = [
    ("ZZ_ACT", "actual", 1),
    ("ZZ_OLD", "budget", 0),
    ("ZZ_PLAN", "budget", 1),
]


def test_variance_dataset_with_a_named_actuals_scenario_is_refused():
    result, queries, _ = _run([{"scenario_id": "ZZ_ACT"}], scenarios=_ZZ_SCENARIOS)
    assert queries == []
    assert result["errors"] == [
        "ZZ_ACT is not an active budget scenario, so there is no variance for it."]
    assert result["values"] == [None]


def test_variance_dataset_with_a_named_inactive_budget_is_refused():
    result, queries, _ = _run([{"scenario_id": "ZZ_OLD"}], scenarios=_ZZ_SCENARIOS)
    assert queries == []
    assert result["errors"] == [
        "ZZ_OLD is not an active budget scenario, so there is no variance for it."]


def test_variance_dataset_with_an_unknown_named_scenario_is_refused():
    result, queries, _ = _run([{"scenario_id": "ZZ_PLNA"}], scenarios=_ZZ_SCENARIOS)
    assert queries == []
    assert result["errors"] == [
        "ZZ_PLNA is not an active budget scenario, so there is no variance for it."]


def test_variance_dataset_named_active_budget_is_filtered_and_refusal_is_per_row():
    result, queries, lookups = _run(
        [{"scenario_id": "ZZ_PLAN"}, {"scenario_id": "ZZ_ACT", "account": "ZZ9"}],
        scenarios=_ZZ_SCENARIOS)
    (sql, params), = queries
    assert "AND budget_scenario_id = {sid:String}" in sql
    assert params["param_sid"] == "ZZ_PLAN"
    assert result["errors"] == [
        None, "ZZ_ACT is not an active budget scenario, so there is no variance for it."]
    # one Scenario lookup, however many named rows
    assert lookups == [{"scenario_type": "budget", "is_active": 1}]


def test_variance_dataset_without_scenario_reads_the_one_active_budget():
    result, queries, lookups = _run([{}], active_budgets=["ZZ_B1"])
    assert not result.get("errors")
    (sql, params), = queries
    assert "AND budget_scenario_id = {sid:String}" in sql
    assert params["param_sid"] == "ZZ_B1"
    assert lookups[0] == {"scenario_type": "budget", "is_active": 1}


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
        "Several active budget scenarios belong to FY2026 (ZZ_B1, ZZ_B2); choose one."]


def test_active_budgets_are_looked_up_once_per_call():
    result, queries, lookups = _run(
        [{}, {"measure": "actual_amount"}, {"year": 2027}],
        active_budgets=["ZZ_B1", "ZZ_B2"],
        cycles=[("ZZ_B1", 2026), ("ZZ_B2", 2027)])
    assert not result.get("errors"), result
    assert len(queries) == 3
    assert sorted(p["param_sid"] for _, p in queries) == ["ZZ_B1", "ZZ_B1", "ZZ_B2"]
    # one Scenario and one Budget Cycle lookup, however many years are read
    assert len(lookups) == 2


# -- with no scenario named, the budget is the request year's (PR #217 review 3)
#
# A flat group has no year in its key, so one call can read several years;
# each request row takes the one active budget of its own year.

def test_variance_dataset_rows_of_two_years_read_each_years_budget():
    result, queries, _ = _run(
        [{"year": 2026}, {"year": 2027}],
        active_budgets=["ZZ_B26", "ZZ_B27"],
        cycles=[("ZZ_B26", 2026), ("ZZ_B27", 2027)])
    assert not result.get("errors"), result
    assert len(queries) == 2
    by_sid = {p["param_sid"]: p for _, p in queries}
    assert set(by_sid) == {"ZZ_B26", "ZZ_B27"}
    assert by_sid["ZZ_B26"]["param_y0"] == "2026"
    assert by_sid["ZZ_B27"]["param_y0"] == "2027"
    assert all("AND budget_scenario_id = {sid:String}" in sql for sql, _ in queries)


def test_variance_dataset_without_a_budget_for_the_year_is_refused():
    result, queries, _ = _run(
        [{"year": 2028}], active_budgets=["ZZ_B26", "ZZ_B27"],
        cycles=[("ZZ_B26", 2026), ("ZZ_B27", 2027)])
    assert queries == []
    assert result["errors"] == [
        "No active budget scenario belongs to FY2028, so there is no variance to show."]


def test_variance_dataset_with_several_budgets_for_the_year_is_refused_by_name():
    result, queries, _ = _run(
        [{"year": 2026}], active_budgets=["ZZ_B26", "ZZ_B26X", "ZZ_B27"],
        cycles=[("ZZ_B26", 2026), ("ZZ_B26X", 2026), ("ZZ_B27", 2027)])
    assert queries == []
    assert result["errors"] == [
        "Several active budget scenarios belong to FY2026 (ZZ_B26, ZZ_B26X); choose one."]


def test_variance_dataset_refusal_is_per_row():
    # FY2026 has a budget, FY2028 has none: only the FY2028 row is refused
    result, queries, _ = _run(
        [{"year": 2026}, {"year": 2028}], active_budgets=["ZZ_B26"],
        cycles=[("ZZ_B26", 2026)])
    (_, params), = queries
    assert params["param_sid"] == "ZZ_B26"
    assert result["errors"] == [
        None,
        "No active budget scenario belongs to FY2028, so there is no variance to show."]


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


# -- the single-cell flat read (api.epm_value) shows a refusal (PR #217 review 2)

def _epm_value(api):
    """epm_value for one flat variance cell, with the Dataset and entity
    checks passed; returns the value dict or the raised refusal."""
    api._assert_entity_access = lambda entity: None
    api._resolve_and_validate = (
        lambda fact, scenario, measure, dims: (_FACTS[0], None))
    try:
        return api.epm_value(
            "ZZ01", 2026, 1, "ZZ0", measure="variance_amount",
            fact="variance_analysis", scenario="variance")
    except _ValidationError as exc:
        return exc


def test_single_cell_variance_with_several_active_budgets_is_refused():
    result, queries, _ = _run([], active_budgets=["ZZ_B1", "ZZ_B2"],
                              call=_epm_value)
    assert queries == []
    assert isinstance(result, _ValidationError), result
    assert str(result) == (
        "Several active budget scenarios belong to FY2026 (ZZ_B1, ZZ_B2); choose one.")


def test_single_cell_variance_with_no_active_budget_is_refused():
    result, queries, _ = _run([], active_budgets=[], call=_epm_value)
    assert queries == []
    assert isinstance(result, _ValidationError), result
    assert str(result) == _NO_BUDGET


def test_single_cell_variance_with_one_active_budget_returns_the_value():
    result, queries, _ = _run([], active_budgets=["ZZ_B1"], call=_epm_value,
                              reply="ZZ01\t2026\tZZ0\t123.5\n")
    assert result == {"value": 123.5}
    (sql, params), = queries
    assert params["param_sid"] == "ZZ_B1"

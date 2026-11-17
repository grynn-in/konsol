"""K.EPM reads of the consolidated budget and of forecast accuracy (konsol#348 S6 KC1).

S6-Q7 (a), taken under the standing rule: the ``consolidated_budget`` Dataset
reads konsolidat's ``gold_consolidated_budget_fact`` (WC1), the fully
consolidated budget rolled to the group slot. That view keeps every Cycle of
a budget scenario (one per year by rule) and, for a forecast scenario, the
declared current round only. So how the forecast-round guards apply to it:

- a NAMED forecast scenario reads its current round, and needs a submitted
  Current Forecast for the year: without one the view holds nothing and the
  cell would read 0, so the read is refused and names the missing declaration
  (as the ``forecast`` and ``latest_estimate`` reads are);
- a NAMED budget scenario reads its own rows;
- NO scenario named reads the budget scenarios only, never a forecast's
  current round added to the budget (#106 A: a forecast is read by naming
  it; konsol#348 S6 review B1 for the budget fact).

FC-4-2 (13 Nov 2026): ``gold_forecast_accuracy`` and
``gold_forecast_accuracy_group`` (WC2, WC3) hold every approved round side by
side, keyed by ``cycle``. ``K.EPM`` binds scenario_id, never a cycle, so an
unguarded read added the rounds' errors together. A read takes the declared
current round's rows only (S6-Q1 b): the scenario resolves as the forecast
reads do, and the SQL narrows ``(fiscal_year, cycle)`` to the declared
Cycle of each year it reads. Every round's accuracy is in the Forecast
Accuracy report.

The guards key on the warehouse table, so a site Dataset under another name
over the same table is guarded too (konsol#230).

Repro: forecast scenario ZZ_FC with two approved rounds of FY2026, R1 and R2,
R2 declared current; budget scenario ZZ_BUD beside it. Host-run with a stub
frappe and a fake ClickHouse that applies the SQL's scenario and round filters.
"""
import importlib.util
import os
import re
import sys
import types

_spec = importlib.util.spec_from_file_location(
    "_read_path_stub_kc1",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "read_path_stub.py"))
read_path_stub = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(read_path_stub)

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

SCENARIOS = [("ZZ_ACT", "actual"), ("ZZ_BUD", "budget"), ("ZZ_FC", "forecast")]
#: Submitted Current Forecasts as (scenario_id, fiscal_year, budget_cycle).
DECLARED = [("ZZ_FC", 2026, "ZZ-R2")]

CB_TABLE = "epm_gold.gold_consolidated_budget_fact"
ACC_TABLE = "epm_gold.gold_forecast_accuracy"
ACC_GROUP_TABLE = "epm_gold.gold_forecast_accuracy_group"

#: The consolidated budget view: a budget scenario's Cycle, and a forecast's
#: CURRENT round only (WC1 drops R1 before the read sees it).
_CB = [
    {"scenario_id": "ZZ_BUD", "cycle": "ZZ-BUD-2026", "fiscal_year": 2026, "value": -50.0},
    {"scenario_id": "ZZ_FC", "cycle": "ZZ-R2", "fiscal_year": 2026, "value": -110.0},
]
#: The accuracy models: every approved round, side by side.
_ACC = [
    {"scenario_id": "ZZ_FC", "cycle": "ZZ-R1", "fiscal_year": 2026, "value": 7.0},
    {"scenario_id": "ZZ_FC", "cycle": "ZZ-R2", "fiscal_year": 2026, "value": 3.0},
]
TABLES = {CB_TABLE: _CB, ACC_TABLE: _ACC, ACC_GROUP_TABLE: _ACC}

MEASURES = dict(read_path_stub.DEFAULT_MEASURES, forecast_error="sum",
                forecast_abs_error="sum", forecast_amount="sum",
                forecast_error_pct="avg")


def _fact(fact_name, scenario_key, table, measures, has_layer):
    return types.SimpleNamespace(
        fact_name=fact_name, scenario_key=scenario_key, clickhouse_table=table,
        has_scenario_id=1, has_layer=has_layer,
        reroute_table=None, reroute_column=None, reroute_measure=None)


FACTS = [
    _fact("consolidated_budget", "consolidated_budget", CB_TABLE, None, 1),
    _fact("forecast_accuracy", "forecast_accuracy", ACC_TABLE, None, 0),
    _fact("forecast_accuracy_group", "forecast_accuracy_group", ACC_GROUP_TABLE, None, 0),
    # site Datasets under other names over the same tables (konsol#230)
    _fact("zz_cb", "zz_cb", CB_TABLE, None, 1),
    _fact("zz_acc", "zz_acc", ACC_TABLE, None, 0),
]


def _rows_for(sql, params):
    table = re.search(r"FROM (\S+)", sql).group(1)
    rows = TABLES[table]
    named = re.search(r"AND scenario_id = \{(\w+):String\}", sql)
    if named:
        rows = [r for r in rows if r["scenario_id"] == params["param_" + named.group(1)]]
    listed = re.search(r"AND scenario_id IN \(([^)]*)\)", sql)
    if listed:
        keep = {params["param_" + n] for n in re.findall(r"\{(\w+):String\}", listed.group(1))}
        rows = [r for r in rows if r["scenario_id"] in keep]
    rounds = re.search(r"AND \(fiscal_year, cycle\) IN \((.*?)\)(?: AND| GROUP|$)", sql)
    if rounds:
        pairs = re.findall(r"\(\{(\w+):Int32\}, \{(\w+):String\}\)", rounds.group(1))
        keep = {(int(params["param_" + y]), params["param_" + c]) for y, c in pairs}
        rows = [r for r in rows if (r["fiscal_year"], r["cycle"]) in keep]
    return rows


class _Throw(Exception):
    pass


def _frappe(scenarios, declared, lookups):
    def get_all(doctype, filters=None, pluck=None, fields=None, **kw):
        filters = dict(filters or {})
        lookups.append((doctype, filters, tuple(fields or ())))
        if doctype == "Scenario":
            return sorted(sid for sid, kind in scenarios
                          if all({"scenario_id": sid, "scenario_type": kind}[k] == v
                                 for k, v in filters.items()))
        if doctype == "Current Forecast":
            assert filters == {"docstatus": 1}, filters
            return [{"scenario_id": s, "fiscal_year": y, "budget_cycle": c}
                    for s, y, c in declared]
        if doctype == "Budget Cycle":
            return []
        raise AssertionError(doctype)

    fake = types.ModuleType("frappe")
    fake.get_all = get_all
    read_path_stub.install(fake, measures=MEASURES)
    fake.whitelist = lambda *a, **k: (lambda fn: fn)
    fake.log_error = lambda *a, **k: None
    fake.get_traceback = lambda: ""
    fake.throw = lambda msg, exc=_Throw, **kw: (_ for _ in ()).throw(exc(msg))
    fake.ValidationError = _Throw
    utils = types.ModuleType("frappe.utils")
    utils.now_datetime = lambda: None
    fake.utils = utils
    return fake, utils


def flat(rows, scenarios=SCENARIOS, declared=DECLARED):
    """api._batch_query_clickhouse over ``rows`` (overrides of a default
    consolidated_budget request, FY2026 P7, group ZZG, account ZZ4000).
    Returns (result, [sql, ...], lookups)."""
    queries, lookups = [], []
    fake, utils = _frappe(scenarios, declared, lookups)
    stubs = {
        "frappe": fake, "frappe.utils": utils,
        "konsol.clickhouse": types.SimpleNamespace(
            connection_url=lambda s: "", get_connection=lambda: {}),
    }
    saved = {k: sys.modules.get(k) for k in stubs}
    before = set(sys.modules)
    sys.modules.update(stubs)
    try:
        spec = importlib.util.spec_from_file_location("_host_api_s6_kc1", os.path.join(APP_DIR, "api.py"))
        api = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(api)
        by_name = {f.fact_name: f for f in FACTS}
        by_key = {f.scenario_key: f for f in FACTS}
        api._get_fact = lambda fact=None, scenario=None: by_name.get(fact)
        api._get_fact_by_scenario = lambda scenario: by_key.get(scenario)
        api._get_ch_connection = lambda: {}

        def fake_query(sql, params, ch_settings):
            queries.append(sql)
            hit = _rows_for(sql, params)
            if not hit:
                return ""
            return f"ZZG\t2026\t7\tZZ4000\t{sum(r['value'] for r in hit)}\n"

        api._clickhouse_query = fake_query
        reqs = []
        for row in rows:
            req = {"entity": "ZZG", "year": 2026, "account": "ZZ4000",
                   "measure": "consolidated_amount", "periods": (7,), "dimensions": {},
                   "fact": "consolidated_budget"}
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


def _no_declaration(sid, year):
    return (f"Scenario '{sid}' has no submitted Current Forecast for FY{year}, "
            "so it has no current round to read. Submit a Current Forecast "
            "naming the round.")


# ── consolidated_budget ─────────────────────────────────────────────────────

def test_consolidated_budget_reads_the_view_and_binds_scenario_and_layer():
    result, queries, _ = flat([{"scenario_id": "ZZ_BUD", "layer": "base"}])
    assert not result.get("errors"), result
    (sql,) = queries
    assert f"FROM {CB_TABLE} " in sql
    assert "AND scenario_id = {sid:String}" in sql
    assert "AND layer = {layer:String}" in sql
    assert "consolidated_amount" in sql


def test_consolidated_budget_named_budget_scenario_reads_its_rows():
    for fact in ("consolidated_budget", "zz_cb"):
        result, _, _ = flat([{"fact": fact, "scenario_id": "ZZ_BUD"}])
        assert not result.get("errors"), (fact, result)
        assert result["values"] == [-50.0], fact


def test_consolidated_budget_without_scenario_reads_the_budgets_only():
    """Before: -160 (the budget plus the forecast's current round). After:
    the budget only; a forecast is read by naming it (#106 A)."""
    for fact in ("consolidated_budget", "zz_cb"):
        result, queries, _ = flat([{"fact": fact}])
        assert not result.get("errors"), (fact, result)
        assert result["values"] == [-50.0], fact
        (sql,) = queries
        assert "AND scenario_id IN (" in sql, fact


def test_consolidated_budget_named_forecast_reads_its_declared_round():
    for fact in ("consolidated_budget", "zz_cb"):
        result, queries, _ = flat([{"fact": fact, "scenario_id": "ZZ_FC"}])
        assert not result.get("errors"), (fact, result)
        assert result["values"] == [-110.0], fact
        assert len(queries) == 1


def test_consolidated_budget_named_forecast_without_declaration_is_refused():
    """The view holds nothing for an undeclared (scenario, year): read, the
    cell came back 0, a missing declaration looking like a zero forecast."""
    for fact in ("consolidated_budget", "zz_cb"):
        result, queries, _ = flat([{"fact": fact, "scenario_id": "ZZ_FC", "year": 2027}])
        assert queries == [], fact
        assert result["errors"] == [_no_declaration("ZZ_FC", 2027)], fact
        assert result["values"] == [None], fact


def test_consolidated_budget_without_scenario_and_only_forecasts_is_refused():
    result, queries, _ = flat([{}], scenarios=[("ZZ_FC", "forecast")])
    assert queries == []
    assert result["errors"] == [
        "Every Scenario is a forecast, so there is no budget to read. "
        "Read a forecast with the 'forecast' scenario or 'latest_estimate'."]


# ── forecast_accuracy / forecast_accuracy_group ─────────────────────────────

_ACCURACY_FACTS = ("forecast_accuracy", "forecast_accuracy_group", "zz_acc")


def test_accuracy_read_takes_the_declared_round_only():
    """Before: 10 (R1's error 7 plus R2's 3). After: R2's 3."""
    for fact in _ACCURACY_FACTS:
        result, queries, _ = flat([{"fact": fact, "measure": "forecast_error",
                                    "scenario_id": "ZZ_FC"}])
        assert not result.get("errors"), (fact, result)
        assert result["values"] == [3.0], fact
        (sql,) = queries
        assert "AND (fiscal_year, cycle) IN (" in sql, fact
        assert "forecast_error" in sql, fact


def test_accuracy_read_without_scenario_takes_the_one_declared_scenario():
    for fact in _ACCURACY_FACTS:
        result, queries, lookups = flat([{"fact": fact, "measure": "forecast_error"}])
        assert not result.get("errors"), (fact, result)
        assert result["values"] == [3.0], fact
        result, _, lookups = flat([{"fact": fact, "measure": "forecast_error"},
                                   {"fact": fact, "measure": "forecast_error", "account": "ZZ4001"}])
        assert [d for d, _, _ in lookups].count("Current Forecast") <= 2, fact


def test_accuracy_read_with_no_declaration_is_refused():
    for fact in _ACCURACY_FACTS:
        result, queries, _ = flat([{"fact": fact, "measure": "forecast_error",
                                    "scenario_id": "ZZ_FC", "year": 2027}])
        assert queries == [], fact
        assert result["errors"] == [_no_declaration("ZZ_FC", 2027)], fact


def test_accuracy_read_of_a_budget_scenario_is_refused():
    for fact in _ACCURACY_FACTS:
        result, queries, _ = flat([{"fact": fact, "measure": "forecast_error",
                                    "scenario_id": "ZZ_BUD"}])
        assert queries == [], fact
        assert result["errors"] == [_no_declaration("ZZ_BUD", 2026)], fact


def test_accuracy_read_over_two_years_binds_each_years_round():
    declared = DECLARED + [("ZZ_FC", 2027, "ZZ-R2-27")]
    result, queries, _ = flat([
        {"fact": "forecast_accuracy", "measure": "forecast_error", "scenario_id": "ZZ_FC"},
        {"fact": "forecast_accuracy", "measure": "forecast_error", "scenario_id": "ZZ_FC",
         "year": 2027}], declared=declared)
    assert not result.get("errors"), result
    (sql,) = queries
    rounds = re.search(r"AND \(fiscal_year, cycle\) IN \((.*?)\)(?: AND| GROUP|$)", sql).group(1)
    assert len(re.findall(r"\(\{\w+:Int32\}, \{\w+:String\}\)", rounds)) == 2, sql


# ── forecast_accuracy_group: the entity argument is the consolidation group ──
#
# konsolidat's gold_forecast_accuracy_group (WC3) keeps one row per
# consolidation group AND entity: an entity in two groups (a sub-group and its
# parent, or two roots) has a row in each group's currency. Read by entity,
# K.EPM added the two currencies together. The read binds the entity argument
# to consolidation_group instead, as the consolidated and consolidated_budget
# reads put the group in the entity slot: the group's accuracy in its own
# currency, its entities summed.

def test_group_accuracy_read_binds_the_consolidation_group():
    result, queries, _ = flat([{"fact": "forecast_accuracy_group", "measure": "forecast_error",
                                "scenario_id": "ZZ_FC"}])
    assert not result.get("errors"), result
    assert result["values"] == [3.0]
    (sql,) = queries
    assert sql.startswith("SELECT consolidation_group, fiscal_year, fiscal_period, main_account")
    assert "WHERE (consolidation_group, fiscal_year, main_account) IN (" in sql
    assert "data_area_id" not in sql


def test_entity_accuracy_read_still_binds_the_entity():
    _, queries, _ = flat([{"fact": "forecast_accuracy", "measure": "forecast_error",
                           "scenario_id": "ZZ_FC"}])
    (sql,) = queries
    assert "WHERE (data_area_id, fiscal_year, main_account) IN (" in sql
    assert "consolidation_group" not in sql

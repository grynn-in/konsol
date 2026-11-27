"""K.EPM budget and forecast reads that read a wrong number refuse instead
(konsol#563, konsol#565, konsol#559; all part of #348).

- #563: a `budget` read naming no scenario summed every non-forecast scenario
  of the table. A year with two active budget scenarios read both added
  together. It now resolves the year's one budget scenario the way the
  variance and consolidated_budget reads do (choose_budget_scenario) and
  refuses several by name.
- #565: the entity slot of the group-keyed Datasets (consolidated,
  consolidated_budget, forecast_accuracy_group) takes a Consolidation Group
  code. An entity code matched no row and read 0.0. It is refused, naming the
  Dataset and the slot.
- #559: a latest_estimate cell whose periods are all on the actual side of
  the declared round (at or before the as-of period) and whose entity has no
  trial balance read 0.0 where the actuals read says "No trial balance".
  It returns the same message and the same no_data code.

Host-run: api.py on a stub frappe and a fake ClickHouse that applies the SQL's
scenario filter.
"""
import importlib.util
import os
import re
import sys
import types

_spec = importlib.util.spec_from_file_location(
    "_read_path_stub_refusals",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "read_path_stub.py"))
read_path_stub = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(read_path_stub)

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

SCENARIOS = [("ZZ_ACT", "actual"), ("ZZ_BUD", "budget"), ("ZZ_BUD2", "budget"),
             ("ZZ_FC", "forecast")]
#: Budget Cycles as (scenario_id, fiscal_year).
CYCLES = [("ZZ_BUD", 2026)]
#: Submitted Current Forecasts as (scenario_id, fiscal_year, budget_cycle, as_of_date).
DECLARED = [("ZZ_FC", 2026, "ZZ-R2", "2026-06-30")]
GROUPS = ["ZZG"]

SPREAD = "epm_gold.gold_spread_budget"
LE = "epm_gold.gold_latest_estimate_current"
CB = "epm_gold.gold_consolidated_budget_fact"
CONS = "epm_gold.gold_consolidated_fact"
ACC_GROUP = "epm_gold.gold_forecast_accuracy_group"
TB = "epm_gold.zz_tb"

ROWS = [
    {"scenario_id": "ZZ_BUD", "value": -50.0},
    {"scenario_id": "ZZ_BUD2", "value": -70.0},
    {"scenario_id": "ZZ_FC", "value": -110.0},
]
MEASURES = dict(read_path_stub.DEFAULT_MEASURES, forecast_error="sum")


def _fact(name, key, table, has_layer=0):
    return types.SimpleNamespace(
        fact_name=name, scenario_key=key, clickhouse_table=table,
        has_scenario_id=1, has_layer=has_layer,
        reroute_table=None, reroute_column=None, reroute_measure=None)


FACTS = [
    _fact("budget_input", "budget", SPREAD, 1),
    _fact("latest_estimate", "latest_estimate", LE),
    _fact("actuals", "actuals", TB),
    _fact("consolidated", "consolidated", CONS),
    _fact("consolidated_budget", "consolidated_budget", CB, 1),
    _fact("forecast_accuracy_group", "forecast_accuracy_group", ACC_GROUP),
]


class _Throw(Exception):
    pass


def _month_end(p):
    return {1: 31, 2: 28, 3: 31, 4: 30, 5: 31, 6: 30, 7: 31, 8: 31, 9: 30,
            10: 31, 11: 30, 12: 31}[p]


def _frappe(scenarios, cycles, declared, groups):
    def get_all(doctype, filters=None, pluck=None, fields=None, **kw):
        filters = dict(filters or {})
        if doctype == "Scenario":
            return sorted(sid for sid, kind in scenarios
                          if all({"scenario_id": sid, "scenario_type": kind,
                                  "is_active": 1}[k] == v for k, v in filters.items()))
        if doctype == "Current Forecast":
            return [{"scenario_id": s, "fiscal_year": y, "budget_cycle": c,
                     "as_of_date": d} for s, y, c, d in declared]
        if doctype == "Budget Cycle":
            wanted = set(filters["scenario_id"][1])
            return [{"scenario_id": s, "fiscal_year": y} for s, y in cycles if s in wanted]
        if doctype == "Consolidation Group":
            return list(groups) if pluck else [{"consolidation_group": g} for g in groups]
        raise AssertionError(doctype)

    fake = types.ModuleType("frappe")
    fake.get_all = get_all
    read_path_stub.install(fake, measures=MEASURES)
    installed = fake.get_all

    def with_dates(doctype=None, *args, **kwargs):
        # The as-of lookup: the Regular period holding a date (the stub's
        # calendar rows carry no dates, so answer it here).
        filters = kwargs.get("filters") or {}
        if doctype == "EPM Fiscal Year Period" and "start_date" in filters:
            day = filters["start_date"][1]
            assert filters["end_date"][1] == day
            year, month = int(day[:4]), int(day[5:7])
            return [{"parent": str(year), "fiscal_period": month}]
        return installed(doctype, *args, **kwargs)

    fake.get_all = with_dates
    fake.whitelist = lambda *a, **k: (lambda fn: fn)
    fake.log_error = lambda *a, **k: None
    fake.get_traceback = lambda: ""
    fake.throw = lambda msg, exc=_Throw, **kw: (_ for _ in ()).throw(exc(msg))
    fake.ValidationError = _Throw
    utils = types.ModuleType("frappe.utils")
    utils.now_datetime = lambda: None
    fake.utils = utils
    return fake, utils


def read(rows, scenarios=SCENARIOS, cycles=CYCLES, declared=DECLARED,
         groups=GROUPS, loaded=(("ZZ01", 2026, 1),), table_rows=ROWS):
    """api._batch_query_clickhouse over ``rows`` (overrides of a default
    budget_input request: entity ZZ01, FY2026 P7, account ZZ4000).
    ``loaded`` is the trial-balance coverage the fake warehouse reports.
    Returns (result, [data sql, ...])."""
    queries = []
    fake, utils = _frappe(scenarios, cycles, declared, groups)
    stubs = {
        "frappe": fake, "frappe.utils": utils,
        "konsol.clickhouse": types.SimpleNamespace(
            connection_url=lambda s: "", get_connection=lambda: {}),
    }
    saved = {k: sys.modules.get(k) for k in stubs}
    before = set(sys.modules)
    sys.modules.update(stubs)
    try:
        spec = importlib.util.spec_from_file_location(
            "_host_api_refusals", os.path.join(APP_DIR, "api.py"))
        api = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(api)
        by_name = {f.fact_name: f for f in FACTS}
        by_key = {f.scenario_key: f for f in FACTS}
        api._get_fact = lambda fact=None, scenario=None: by_name.get(fact)
        api._get_fact_by_scenario = lambda scenario: by_key.get(scenario)
        api._get_ch_connection = lambda: {}

        def fake_query(sql, params, ch_settings):
            if read_path_stub.is_tb_coverage(sql):
                return "".join(f"{e}\t{y}\t{p}\n" for e, y, p in loaded)
            queries.append(sql)
            hit = list(table_rows)
            named = re.search(r"AND scenario_id = \{(\w+):String\}", sql)
            if named:
                hit = [r for r in hit if r["scenario_id"] == params["param_" + named.group(1)]]
            listed = re.search(r"AND scenario_id IN \(([^)]*)\)", sql)
            if listed:
                keep = {params["param_" + n]
                        for n in re.findall(r"\{(\w+):String\}", listed.group(1))}
                hit = [r for r in hit if r["scenario_id"] in keep]
            if not hit:
                return ""
            return f"{params['param_e0']}\t2026\t7\tZZ4000\t{sum(r['value'] for r in hit)}\n"

        api._clickhouse_query = fake_query
        reqs = []
        for row in rows:
            req = {"entity": "ZZ01", "year": 2026, "account": "ZZ4000",
                   "measure": "period_amount", "periods": (7,), "dimensions": {},
                   "fact": "budget_input"}
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
    return result, queries


# ── #563: a budget read naming no scenario reads one budget ────────────────

def test_budget_read_with_two_active_budgets_is_refused_by_name():
    """Before: -120 (ZZ_BUD's -50 plus ZZ_BUD2's -70). After: refused."""
    cycles = CYCLES + [("ZZ_BUD2", 2026)]
    result, queries = read([{}], cycles=cycles)
    assert queries == []
    assert result["values"] == [None]
    assert result["errors"] == [
        "Several active budget scenarios belong to FY2026 (ZZ_BUD, ZZ_BUD2); "
        "choose one."]


def test_budget_read_with_one_active_budget_reads_it_by_name():
    result, queries = read([{}])
    assert not result.get("errors"), result
    assert result["values"] == [-50.0]
    (sql,) = queries
    assert "AND scenario_id = {sid:String}" in sql


def test_budget_read_takes_the_years_own_budget():
    """A second budget scenario whose Cycle is another year is not read."""
    result, _ = read([{}], cycles=CYCLES + [("ZZ_BUD2", 2027)])
    assert not result.get("errors"), result
    assert result["values"] == [-50.0]


def test_budget_read_with_no_active_budget_is_refused():
    result, queries = read([{}], cycles=[])
    assert queries == []
    assert result["errors"] == [
        "No active budget scenario belongs to FY2026, so there is no budget "
        "to read. Name scenario_id to read a forecast."]


def test_named_budget_read_is_unchanged_with_two_budgets():
    cycles = CYCLES + [("ZZ_BUD2", 2026)]
    result, _ = read([{"scenario_id": "ZZ_BUD2"}], cycles=cycles)
    assert result["values"] == [-70.0]


# ── #565: a group-keyed Dataset refuses an entity code ─────────────────────

def _group_refusal(fact):
    return (f"Dataset '{fact}' reads by consolidation group: the entity "
            "argument must be a Consolidation Group code, and 'ZZ01' is not "
            "one.")


def test_group_keyed_datasets_refuse_an_entity_code():
    for fact, measure in (("forecast_accuracy_group", "forecast_error"),
                          ("consolidated", "period_amount"),
                          ("consolidated_budget", "period_amount")):
        result, queries = read([{"fact": fact, "measure": measure,
                                 "scenario_id": "ZZ_FC" if fact == "forecast_accuracy_group" else "",
                                 }])
        assert queries == [], fact
        assert result["values"] == [None], fact
        assert result["errors"] == [_group_refusal(fact)], fact


def test_group_keyed_dataset_reads_a_group_code():
    result, _ = read([{"fact": "consolidated", "entity": "ZZG"}])
    assert not result.get("errors"), result


# ── #559: latest estimate where the actual side has no trial balance ───────

def _le(**over):
    req = {"fact": "latest_estimate", "scenario_id": "ZZ_FC", "periods": (3,)}
    req.update(over)
    return req


def test_latest_estimate_actual_side_without_trial_balance_is_no_data():
    """P3 is at or before the as-of period (P6) and ZZ01 has a trial balance
    for P1 only. Before: 0.0. After: the actuals read's message and code."""
    result, queries = read([_le()])
    assert queries == []
    assert result["values"] == [None]
    assert result["errors"] == ["No trial balance for ZZ01 2026 P3"]
    assert result["error_codes"] == ["no_data"]


def test_latest_estimate_actual_side_with_trial_balance_reads():
    result, _ = read([_le(periods=(1,))])
    assert not result.get("errors"), result


def test_latest_estimate_forecast_side_is_not_checked():
    """P9 is after the as-of period: no trial balance is expected."""
    result, queries = read([_le(periods=(9,))], loaded=())
    assert not result.get("errors"), result
    assert len(queries) == 1


def test_latest_estimate_range_across_the_as_of_is_not_refused():
    result, queries = read([_le(periods=(5, 6, 7))], loaded=())
    assert not result.get("errors"), result
    assert len(queries) == 1


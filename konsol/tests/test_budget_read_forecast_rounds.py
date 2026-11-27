"""Budget and forecast reads never sum forecast rounds (konsol#348, S6-Q1 b).

S6-Q1 (b), decided by Deepak Pai on 17 Nov 2026: a forecast (scenario,
fiscal year) has a declared Current Forecast, so its approved rounds never sum.
The S6 pair A review (finding B1) found two readers that still summed them:

- the `budget` reads (the flat `budget_input` Dataset over gold_spread_budget
  and the hierarchy `budget` config over gold_budget_at_hierarchy_node) bind
  scenario_id and layer but never cycle, so a forecast scenario id read every
  approved round added together;
- a `budget` read that named no scenario read every scenario in the table,
  forecasts included.

The repro is the reviewer's: a forecast scenario ZZ_FC with two approved
rounds of FY2026, R1 (credit 100) and R2 (credit 110), R2 declared current, and
a budget scenario ZZ_BUD (credit 50) beside them. The fake warehouse below
holds those rows and applies the scenario filter the SQL carries, so a test
reads the number a reader would see, not only the SQL text.

The modules are loaded under private names with a stub frappe (Scenario and
Current Forecast) and a fake ClickHouse.
"""
import importlib.util
import os
import re
import sys
import types
from collections import defaultdict

_spec = importlib.util.spec_from_file_location(
    "_read_path_stub",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "read_path_stub.py"))
read_path_stub = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(read_path_stub)

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: The reviewer's repro: Scenario records as (scenario_id, scenario_type).
SCENARIOS = [("ZZ_ACT", "actual"), ("ZZ_BUD", "budget"), ("ZZ_FC", "forecast")]
#: Submitted Current Forecasts as (scenario_id, fiscal_year).
DECLARED = [("ZZ_FC", 2026)]
#: Budget Cycles as (name, scenario_id, fiscal_year, docstatus): ZZ_BUD's
#: approved Cycle, the one budget scenario of FY2026 (konsol#563).
CYCLES = [("ZZ-BUD-2026", "ZZ_BUD", 2026, 1)]

#: Signed amounts (credit negative) per scenario and cycle, one account.
_SPREAD = [
    {"scenario_id": "ZZ_BUD", "cycle": "ZZ-BUD-2026", "amount": -50.0},
    {"scenario_id": "ZZ_FC", "cycle": "ZZ-R1", "amount": -100.0},
    {"scenario_id": "ZZ_FC", "cycle": "ZZ-R2", "amount": -110.0},
]
#: What each relation holds. The current-round views hold the declared round
#: only (konsolidat S6 WA6); the fact and the budget rollup hold every round.
TABLES = {
    "epm_gold.gold_spread_budget": _SPREAD,
    "epm_gold.gold_budget_at_hierarchy_node": _SPREAD,
    "epm_gold.gold_forecast_current": [r for r in _SPREAD if r["cycle"] != "ZZ-R1"
                                       and r["scenario_id"] == "ZZ_FC"],
    "epm_gold.gold_forecast_current_at_hierarchy_node": [
        r for r in _SPREAD if r["cycle"] == "ZZ-R2"],
    "epm_gold.gold_latest_estimate_current": [
        r for r in _SPREAD if r["cycle"] == "ZZ-R2"],
}


def _fact(fact_name, scenario_key, table, has_layer=1, has_scenario_id=1):
    return types.SimpleNamespace(
        fact_name=fact_name, scenario_key=scenario_key, clickhouse_table=table,
        has_scenario_id=has_scenario_id, has_layer=has_layer,
        reroute_table=None, reroute_column=None, reroute_measure=None,
    )


#: The shipped Datasets' identities (konsol/defaults/dataset.json).
FACTS = [
    _fact("budget_input", "budget", "epm_gold.gold_spread_budget"),
    _fact("forecast_input", "forecast", "epm_gold.gold_forecast_current"),
    _fact("latest_estimate", "latest_estimate",
          "epm_gold.gold_latest_estimate_current", has_layer=0),
    # Site Datasets under other names over the same tables (konsol#230): the
    # guards follow the table a read resolves to, not the Dataset name.
    _fact("zz_budget", "zz_budget", "epm_gold.gold_spread_budget"),
    _fact("zz_forecast", "zz_forecast", "epm_gold.gold_forecast_current"),
    _fact("zz_le", "zz_le", "epm_gold.gold_latest_estimate_current", has_layer=0),
    # A site Dataset over the budget fact that declares no scenario column.
    _fact("zz_budget_noscn", "zz_budget_noscn", "epm_gold.gold_spread_budget",
          has_scenario_id=0),
]


def _scenario_rows(sql, params):
    """The rows of the table ``sql`` reads that its scenario filter keeps."""
    table = re.search(r"FROM (\S+)", sql).group(1)
    rows = TABLES[table]
    named = re.search(r"AND scenario_id = \{(\w+):String\}", sql)
    if named:
        rows = [r for r in rows if r["scenario_id"] == params["param_" + named.group(1)]]
    listed = re.search(r"AND scenario_id IN \(([^)]*)\)", sql)
    if listed:
        keep = {params["param_" + n] for n in re.findall(r"\{(\w+):String\}", listed.group(1))}
        rows = [r for r in rows if r["scenario_id"] in keep]
    return rows


class _Throw(Exception):
    pass


def _matches(value, condition):
    """A frappe filter value: a plain value, ["in", [...]] or ["<", n]."""
    if isinstance(condition, list):
        op, arg = condition
        return value in arg if op == "in" else value < arg
    return value == condition


def _frappe(scenarios, declared, lookups, cycles=CYCLES):
    def get_all(doctype, filters=None, pluck=None, fields=None, **kw):
        filters = dict(filters or {})
        lookups.append((doctype, filters))
        if doctype == "Scenario":
            found = [sid for sid, kind in scenarios
                     if all(({"scenario_id": sid, "scenario_type": kind, "is_active": 1}[k] == v)
                            for k, v in filters.items())]
            return sorted(found)
        if doctype == "Current Forecast":
            assert filters == {"docstatus": 1}, filters
            # as of 2025-12-31: the FY2026 periods read here are forecast side
            return [{"scenario_id": s, "fiscal_year": y, "as_of_date": "2025-12-31"}
                    for s, y in declared]
        if doctype == "Budget Cycle":
            found = []
            for name, sid, year, docstatus in cycles:
                record = {"name": name, "scenario_id": sid, "fiscal_year": year,
                          "docstatus": docstatus}
                if all(_matches(record[k], v) for k, v in filters.items()):
                    found.append({k: record[k] for k in ("name", "scenario_id", "fiscal_year")})
            return found
        raise AssertionError(doctype)

    fake = types.ModuleType("frappe")
    fake.get_all = get_all
    read_path_stub.install(fake)
    installed = fake.get_all

    def with_dates(doctype=None, *args, **kwargs):
        # the as-of lookup (konsol#559): the Regular period holding a date
        filters = kwargs.get("filters") or {}
        if doctype == "EPM Fiscal Year Period" and "start_date" in filters:
            day = filters["start_date"][1]
            return [{"parent": day[:4], "fiscal_period": int(day[5:7])}]
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


def _load(path, name, stubs):
    saved = {k: sys.modules.get(k) for k in stubs}
    sys.modules.update(stubs)
    spec = importlib.util.spec_from_file_location(name, os.path.join(APP_DIR, path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod, saved


def _restore(saved):
    for k, mod in saved.items():
        if mod is None:
            sys.modules.pop(k, None)
        else:
            sys.modules[k] = mod


def flat(rows, scenarios=SCENARIOS, declared=DECLARED, cycles=CYCLES):
    """api._batch_query_clickhouse over ``rows`` (dicts overriding a default
    request of the budget_input Dataset, FY2026 P1, entity ZZ01, account
    ZZ4000). Returns (result, [sql, ...], lookups)."""
    queries, lookups = [], []
    fake, utils = _frappe(scenarios, declared, lookups, cycles)
    stubs = {
        "frappe": fake, "frappe.utils": utils,
        "konsol.clickhouse": types.SimpleNamespace(
            connection_url=lambda s: "", get_connection=lambda: {}),
    }
    before = set(sys.modules)
    api, saved = _load("api.py", "_host_api_s6_b1", stubs)
    try:
        by_name = {f.fact_name: f for f in FACTS}
        by_key = {f.scenario_key: f for f in FACTS}
        api._get_fact = lambda fact=None, scenario=None: by_name.get(fact)
        api._get_fact_by_scenario = lambda scenario: by_key.get(scenario)
        api._get_ch_connection = lambda: {}

        def fake_query(sql, params, ch_settings):
            queries.append(sql)
            total = sum(r["amount"] for r in _scenario_rows(sql, params))
            rows_ = _scenario_rows(sql, params)
            if not rows_:
                return ""
            return f"ZZ01\t2026\t1\tZZ4000\t{total}\n"

        api._clickhouse_query = fake_query
        reqs = []
        for row in rows:
            req = {"entity": "ZZ01", "year": 2026, "account": "ZZ4000",
                   "measure": "period_amount", "periods": (1,), "dimensions": {},
                   "fact": "budget_input"}
            req.update(row)
            reqs.append(req)
        result = api._batch_query_clickhouse(reqs)
    finally:
        for key in set(sys.modules) - before:
            if key.split(".")[0] in ("frappe", "konsol"):
                del sys.modules[key]
        _restore(saved)
    return result, queries, lookups


def hierarchy(rows, scenarios=SCENARIOS, declared=DECLARED, cycles=CYCLES):
    """hierarchy_query.batch_query_hierarchy over ``rows`` (dicts overriding a
    default `budget` request at node ZZ_N). Returns (result, [sql, ...],
    lookups)."""
    queries, lookups = [], []
    fake, _ = _frappe(scenarios, declared, lookups, cycles)
    stubs = {
        "frappe": fake,
        "konsol.clickhouse": types.SimpleNamespace(get_connection=lambda: {}),
        "konsol.entity_permissions": types.SimpleNamespace(
            entity_read_scope=lambda entity, allowed, wildcard=False: (None, None)),
    }
    hq, saved = _load("hierarchy_query.py", "_host_hq_s6_b1", stubs)
    try:
        def fake_query(sql, params, ch_settings):
            queries.append(sql)
            rows_ = _scenario_rows(sql, params)
            if not rows_:
                return ""
            return f"ZZ01\t2026\tZZ4000\t{sum(r['amount'] for r in rows_)}\n"

        hq._clickhouse_query = fake_query
        reqs = []
        for row in rows:
            req = {"entity": "ZZ01", "year": 2026, "periods": (1,), "account": "ZZ4000",
                   "scenario": "budget", "hierarchy_name": "ZZ_H",
                   "hierarchy_node": "ZZ_N", "measure": "period_amount"}
            req.update(row)
            reqs.append(req)
        result = hq.batch_query_hierarchy(reqs, allowed_entities=None)
    finally:
        _restore(saved)
    return result, queries, lookups


FORECAST_ON_BUDGET = (
    "Scenario 'ZZ_FC' is a forecast, and the budget read sums every approved "
    "round of a forecast year. Read it with the 'forecast' scenario (the "
    "declared current round) or 'latest_estimate' instead.")


# ── B1: a budget read with a forecast scenario id is refused ───────────────

def test_flat_budget_read_refuses_a_forecast_scenario_id():
    result, queries, _ = flat([{"scenario_id": "ZZ_FC"}])
    assert queries == []
    assert result["errors"] == [FORECAST_ON_BUDGET]
    assert result["values"] == [None]


def test_hierarchy_budget_read_refuses_a_forecast_scenario_id():
    result, queries, _ = hierarchy([{"scenario_id": "ZZ_FC"}])
    assert queries == []
    assert result["errors"] == [FORECAST_ON_BUDGET]
    assert result["values"] == [None]


def test_a_named_budget_scenario_still_reads_its_own_rows():
    for run in (flat, hierarchy):
        result, queries, _ = run([{"scenario_id": "ZZ_BUD"}])
        assert not result.get("errors"), run
        assert result["values"] == [-50.0], run
        (sql,) = queries
        assert "AND scenario_id = {sid:String}" in sql


# ── B1: a budget read with no scenario id excludes the forecasts ───────────

def test_flat_budget_read_without_scenario_excludes_forecasts():
    """Before: -260 (budget 50 + R1 100 + R2 110). After: the budget only."""
    result, queries, lookups = flat([{}])
    assert not result.get("errors")
    assert result["values"] == [-50.0]
    (sql,) = queries
    # the year's one budget scenario, bound by name (konsol#563)
    assert "AND scenario_id = {sid:String}" in sql
    # one Scenario lookup per kind, however many rows
    result, queries, lookups = flat([{}, {"account": "ZZ4001"}])
    assert len([d for d, _ in lookups if d == "Scenario"]) <= 2


def test_hierarchy_budget_read_without_scenario_excludes_forecasts():
    result, queries, _ = hierarchy([{}])
    assert not result.get("errors")
    assert result["values"] == [-50.0]
    (sql,) = queries
    # the year's one budget scenario, bound by name, as the flat read does
    # (konsol#563, review M1)
    assert "AND scenario_id = {sid:String}" in sql


def test_budget_read_without_scenario_and_only_forecasts_is_refused():
    """One rule on both paths: the year's one active budget scenario, none
    refused the same way (konsol#563, review M1)."""
    only = [("ZZ_FC", "forecast")]
    for run in (flat, hierarchy):
        result, queries, _ = run([{}], scenarios=only)
        assert queries == [], run
        assert result["errors"] == [
            "No active budget scenario belongs to FY2026, so there is no budget "
            "to read. Name scenario_id to read a forecast."], run


_TWO_BUDGETS = SCENARIOS + [("ZZ_BUD2", "budget")]


def test_budget_read_without_scenario_and_two_budgets_is_refused_on_both_paths():
    """Before, the hierarchy read added ZZ_BUD and ZZ_BUD2 together (review M1)."""
    cycles = CYCLES + [("ZZ-BUD2-2026", "ZZ_BUD2", 2026, 1)]
    for run in (flat, hierarchy):
        result, queries, _ = run([{}], scenarios=_TWO_BUDGETS, cycles=cycles)
        assert queries == [], run
        assert result["values"] == [None], run
        assert result["errors"] == [
            "Several active budget scenarios belong to FY2026 (ZZ_BUD, ZZ_BUD2); "
            "choose one."], run


def test_a_draft_cycle_does_not_make_a_budget_scenario_active():
    """ZZ_BUD2's Cycle is a draft: it has not reached the warehouse, so the
    read takes ZZ_BUD alone, never "several scenarios" (review m1)."""
    cycles = CYCLES + [("ZZ-BUD2-2026", "ZZ_BUD2", 2026, 0)]
    for run in (flat, hierarchy):
        result, queries, lookups = run([{}], scenarios=_TWO_BUDGETS, cycles=cycles)
        assert not result.get("errors"), (run, result)
        assert result["values"] == [-50.0], run
        cycle_filters = [f for d, f in lookups if d == "Budget Cycle"]
        assert cycle_filters and all(f["docstatus"] == 1 for f in cycle_filters), cycle_filters


def test_budget_dataset_without_a_scenario_column_is_refused():
    """A site Dataset over gold_spread_budget with has_scenario_id off cannot
    keep to one budget: before, it read every non-forecast scenario added
    together (review l1)."""
    result, queries, _ = flat([{"fact": "zz_budget_noscn"}])
    assert queries == []
    assert result["values"] == [None]
    assert result["errors"] == [
        "Dataset 'zz_budget_noscn' reads epm_gold.gold_spread_budget, which "
        "holds every budget and forecast scenario, but has_scenario_id is off, "
        "so a read cannot keep to one budget scenario. Turn on has_scenario_id "
        "on the Dataset."]


# ── m2: a forecast read needs a submitted Current Forecast ─────────────────
#
# The forecast and latest_estimate reads go through the current-round views,
# which hold nothing for a (scenario, year) with no declaration. Read there,
# the cell came back 0 (api.py's coalesce): a missing declaration looked like
# a zero forecast. The read is refused and names what is missing, the way a
# variance read with no budget scenario is (choose_budget_scenario).

def _no_declaration(sid, year):
    return (f"Scenario '{sid}' has no submitted Current Forecast for FY{year}, "
            "so it has no current round to read. Submit a Current Forecast "
            "naming the round.")


_NONE_DECLARED = ("No submitted Current Forecast declares a round for FY2027, "
                  "so there is no forecast to read. Submit a Current Forecast "
                  "for the forecast scenario, or name scenario_id.")

#: The forecast reads: (runner, request overrides).
_FORECAST_READS = [
    (flat, {"fact": "forecast_input"}),
    (flat, {"fact": "latest_estimate"}),
    (hierarchy, {"scenario": "forecast"}),
]


def test_forecast_reads_the_declared_round():
    for run, base in _FORECAST_READS:
        result, queries, _ = run([dict(base, scenario_id="ZZ_FC")])
        assert not result.get("errors"), (base, result)
        assert result["values"] == [-110.0], base
        assert len(queries) == 1


def test_forecast_read_with_no_declaration_for_the_year_is_refused():
    for run, base in _FORECAST_READS:
        result, queries, _ = run([dict(base, scenario_id="ZZ_FC", year=2027)])
        assert queries == [], base
        assert result["errors"] == [_no_declaration("ZZ_FC", 2027)], base
        assert result["values"] == [None], base


def test_forecast_read_of_a_budget_scenario_is_refused():
    for run, base in _FORECAST_READS:
        result, queries, _ = run([dict(base, scenario_id="ZZ_BUD")])
        assert queries == [], base
        assert result["errors"] == [_no_declaration("ZZ_BUD", 2026)], base


def test_forecast_read_without_scenario_takes_the_one_declared_scenario():
    for run, base in _FORECAST_READS:
        result, queries, lookups = run([dict(base)])
        assert not result.get("errors"), (base, result)
        assert result["values"] == [-110.0], base
        (sql,) = queries
        assert "AND scenario_id = {sid:String}" in sql, base
        # one Current Forecast lookup however many rows; a latest estimate
        # adds one for its as-of period (konsol#559, review l2)
        expected = 2 if base.get("fact") == "latest_estimate" else 1
        for rows in ([dict(base)], [dict(base), dict(base, account="ZZ4001")]):
            _, _, seen = run(rows)
            assert [d for d, _ in seen].count("Current Forecast") == expected, (base, rows)


def test_forecast_read_without_scenario_and_no_declaration_is_refused():
    for run, base in _FORECAST_READS:
        result, queries, _ = run([dict(base, year=2027)])
        assert queries == [], base
        assert result["errors"] == [_NONE_DECLARED], base


def test_forecast_read_without_scenario_and_two_declarations_is_refused():
    two = [("ZZ_FC", 2026), ("ZZ_FC2", 2026)]
    scenarios = SCENARIOS + [("ZZ_FC2", "forecast")]
    for run, base in _FORECAST_READS:
        result, queries, _ = run([dict(base)], scenarios=scenarios, declared=two)
        assert queries == [], base
        assert result["errors"] == [
            "Several forecast scenarios have a submitted Current Forecast for "
            "FY2026 (ZZ_FC, ZZ_FC2); name one with scenario_id."], base


# ── The guards follow the warehouse table, not the Dataset name ────────────
#
# Datasets belong to the site (konsol#230). A site Dataset named otherwise
# over gold_spread_budget summed forecast rounds, and one over the
# current-round views read 0 for a year with no Current Forecast.

def test_renamed_budget_dataset_refuses_a_forecast_scenario_id():
    result, queries, _ = flat([{"fact": "zz_budget", "scenario_id": "ZZ_FC"}])
    assert queries == []
    assert result["errors"] == [FORECAST_ON_BUDGET]
    assert result["values"] == [None]


def test_renamed_budget_dataset_without_scenario_excludes_forecasts():
    result, queries, _ = flat([{"fact": "zz_budget"}])
    assert not result.get("errors")
    assert result["values"] == [-50.0]
    (sql,) = queries
    assert "AND scenario_id = {sid:String}" in sql


def test_renamed_forecast_dataset_with_no_declaration_is_refused():
    for fact in ("zz_forecast", "zz_le"):
        result, queries, _ = flat([{"fact": fact, "scenario_id": "ZZ_FC", "year": 2027}])
        assert queries == [], fact
        assert result["errors"] == [_no_declaration("ZZ_FC", 2027)], fact
        result, queries, _ = flat([{"fact": fact, "year": 2027}])
        assert queries == [], fact
        assert result["errors"] == [_NONE_DECLARED], fact


def test_renamed_forecast_dataset_reads_the_declared_round():
    for fact in ("zz_forecast", "zz_le"):
        result, queries, _ = flat([{"fact": fact}])
        assert not result.get("errors"), (fact, result)
        assert result["values"] == [-110.0], fact

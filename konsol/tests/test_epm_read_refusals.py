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
#: Budget Cycles as (name, scenario_id, fiscal_year, docstatus).
CYCLES = [("ZZ-BUD-2026", "ZZ_BUD", 2026, 1)]
_BUD2_2026 = ("ZZ-BUD2-2026", "ZZ_BUD2", 2026, 1)
#: The entities each Cycle budgets, as (cycle, entity): its Budget Entries
#: and its Annual Inputs (konsol#568).
ENTRIES = [("ZZ-BUD-2026", "ZZ01"), ("ZZ-BUD2-2026", "ZZ01")]
ANNUAL = [("ZZ-BUD-2026", "ZZ03")]
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
    _fact("variance_analysis", "variance", "epm_gold.gold_variance_analysis"),
]


class _Throw(Exception):
    pass


def _month_end(p):
    return {1: 31, 2: 28, 3: 31, 4: 30, 5: 31, 6: 30, 7: 31, 8: 31, 9: 30,
            10: 31, 11: 30, 12: 31}[p]


def _matches(value, condition):
    """A frappe filter value: a plain value, ["in", [...]] or ["<", n]."""
    if isinstance(condition, list):
        op, arg = condition
        return value in arg if op == "in" else value < arg
    return value == condition


def _frappe(scenarios, cycles, declared, groups, lookups, entries=ENTRIES,
            annual=ANNUAL):
    def get_all(doctype, filters=None, pluck=None, fields=None, **kw):
        filters = dict(filters or {})
        lookups.append(doctype)
        if doctype == "Scenario":
            return sorted(sid for sid, kind in scenarios
                          if all({"scenario_id": sid, "scenario_type": kind,
                                  "is_active": 1}[k] == v for k, v in filters.items()))
        if doctype == "Current Forecast":
            return [{"scenario_id": s, "fiscal_year": y, "budget_cycle": c,
                     "as_of_date": d} for s, y, c, d in declared]
        if doctype == "Budget Cycle":
            found = []
            for name, sid, year, docstatus in cycles:
                record = {"name": name, "scenario_id": sid, "fiscal_year": year,
                          "docstatus": docstatus}
                if all(_matches(record[k], v) for k, v in filters.items()):
                    found.append({k: record[k] for k in ("name", "scenario_id", "fiscal_year")})
            return found
        if doctype in ("Budget Entry", "Budget Annual Input"):
            rows = entries if doctype == "Budget Entry" else annual
            return [{"cycle": c, "data_area_id": e} for c, e in rows
                    if _matches(c, filters["cycle"]) and _matches(e, filters["data_area_id"])]
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
            if day.startswith("2099"):
                return []  # a date no declared Regular period holds
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
         groups=GROUPS, loaded=(("ZZ01", 2026, 1),), table_rows=ROWS,
         lookups=None, calls=None, entries=ENTRIES, annual=ANNUAL):
    """api._batch_query_clickhouse over ``rows`` (overrides of a default
    budget_input request: entity ZZ01, FY2026 P7, account ZZ4000).
    ``loaded`` is the trial-balance coverage the fake warehouse reports.
    ``lookups``, when given, collects the doctype of every frappe.get_all;
    ``calls``, every ClickHouse query, the coverage scan included.
    Returns (result, [data sql, ...])."""
    queries = []
    lookups = [] if lookups is None else lookups
    calls = [] if calls is None else calls
    fake, utils = _frappe(scenarios, cycles, declared, groups, lookups, entries, annual)
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
            calls.append(sql)
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
    cycles = CYCLES + [_BUD2_2026]
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
    result, _ = read([{}], cycles=CYCLES + [("ZZ-BUD2-2027", "ZZ_BUD2", 2027, 1)])
    assert not result.get("errors"), result
    assert result["values"] == [-50.0]


def test_budget_read_with_no_active_budget_is_refused():
    result, queries = read([{}], cycles=[])
    assert queries == []
    assert result["errors"] == [
        "No active budget scenario belongs to FY2026, so there is no budget "
        "to read. Name scenario_id to read a forecast."]


def test_named_budget_read_is_unchanged_with_two_budgets():
    cycles = CYCLES + [_BUD2_2026]
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


def test_group_codes_are_looked_up_once_per_batch():
    """Two group-keyed cells, one Consolidation Group lookup (review l3)."""
    lookups = []
    result, _ = read([{"fact": "consolidated", "entity": "ZZG"},
                      {"fact": "consolidated", "entity": "ZZG", "account": "ZZ4001"}],
                     lookups=lookups)
    assert not result.get("errors"), result
    assert lookups.count("Consolidation Group") == 1


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



def test_latest_estimate_as_of_in_no_regular_period_is_refused():
    """The round's as-of date is in no declared Regular period: the actual
    side cannot be told, so the read refuses and asks nothing of ClickHouse
    (review m2)."""
    calls = []
    result, queries = read([_le()], declared=[("ZZ_FC", 2026, "ZZ-R2", "2099-06-30")],
                           calls=calls)
    assert calls == []
    assert result["values"] == [None]
    assert result["errors"] == [
        "The as-of date of the Current Forecast of 'ZZ_FC' for FY2026 is in no "
        "declared Regular period, so its actual side cannot be told. Declare "
        "the period in EPM Fiscal Year."]


# ── #568: a budget read is refused only where no approved Cycle budgets it ──
#
# Decided by Deepak Pai, 27 Nov 2026: a read is refused when the scenario has
# no approved Budget Cycle for the entity and year; where an approved Cycle
# exists and holds no row, the read is 0 (no budget means budget zero, 22 Sep
# 2026). A Cycle is per (scenario, year); it budgets an entity when it holds a
# Budget Entry or an Annual Input for it. A group-keyed read asks the
# (scenario, year) only.

def no_cycle(sid, year, who):
    return (f"Scenario '{sid}' has no approved Budget Cycle for FY{year}, so "
            f"there is no budget to read for {who}. Approve a Budget Cycle for "
            f"'{sid}' FY{year}.")


def no_entity(sid, year, entity, cycle):
    return (f"Scenario '{sid}' has no approved Budget Cycle for entity "
            f"'{entity}' in FY{year}: approved Cycle {cycle} holds no Budget "
            f"Entry or Annual Input for it, so there is no budget to read. Add "
            f"its budget to a Budget Cycle of '{sid}' FY{year} and approve the "
            "Cycle.")


_DRAFT_BUD2 = CYCLES + [("ZZ-BUD2-2026", "ZZ_BUD2", 2026, 0)]


def test_budget_read_of_a_scenario_with_no_approved_cycle_is_refused():
    """ZZ_BUD2's FY2026 Cycle is a draft: its rows are not in the warehouse,
    and before the read was 0.0."""
    for fact, measure in (("budget_input", "period_amount"),
                          ("variance_analysis", "variance_abs")):
        result, queries = read([{"fact": fact, "measure": measure,
                                 "scenario_id": "ZZ_BUD2"}], cycles=_DRAFT_BUD2)
        assert queries == [], fact
        assert result["values"] == [None], fact
        assert result["errors"] == [no_cycle("ZZ_BUD2", 2026, "entity 'ZZ01'")], fact


def test_variance_read_of_a_scenario_with_no_approved_cycle_for_the_year_is_refused():
    """ZZ_BUD is active (its FY2026 Cycle is approved) but has none for FY2027."""
    result, queries = read([{"fact": "variance_analysis", "measure": "variance_abs",
                             "scenario_id": "ZZ_BUD", "year": 2027}])
    assert queries == []
    assert result["errors"] == [no_cycle("ZZ_BUD", 2027, "entity 'ZZ01'")]


def test_budget_read_of_an_entity_the_approved_cycle_does_not_budget_is_refused():
    for scenario_id in ("", "ZZ_BUD"):
        result, queries = read([{"entity": "ZZ02", "scenario_id": scenario_id}])
        assert queries == [], scenario_id
        assert result["values"] == [None], scenario_id
        assert result["errors"] == [no_entity("ZZ_BUD", 2026, "ZZ02", "ZZ-BUD-2026")], \
            scenario_id


def test_variance_read_of_an_entity_with_no_entries_is_not_refused():
    """Decided by Deepak Pai, 27 Nov 2026 (konsol#568): variance keeps the
    22 Sep rule "unbudgeted is unfavourable". An approved Cycle for the
    scenario and year is all a variance cell needs; an entity with actuals and
    no Budget Entry or Annual Input reads variance = actual."""
    for scenario_id in ("", "ZZ_BUD"):
        result, queries = read(
            [{"fact": "variance_analysis", "measure": "variance_abs", "entity": "ZZ02",
              "scenario_id": scenario_id}],
            table_rows=[{"scenario_id": "ZZ_BUD", "value": -40.0}])
        assert not result.get("errors"), (scenario_id, result)
        assert result["values"] == [-40.0], scenario_id
        assert len(queries) == 1, scenario_id


def _hq():
    spec = importlib.util.spec_from_file_location(
        "_host_hq_refusals", os.path.join(APP_DIR, "hierarchy_query.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_hierarchy_variance_coverage_asks_the_cycle_only():
    """The shared helper (flat and hierarchy reads): a variance need with a
    named entity the Cycle does not budget is not refused; a budget need is;
    a variance need with no approved Cycle is refused either way."""
    hq = _hq()
    approved = {("ZZ_BUD", 2026): ["ZZ-BUD-2026"]}
    budgeted = {("ZZ_BUD", 2026): {"ZZ01"}}

    def look(kind, sid="ZZ_BUD"):
        hq._approved_budget_coverage = lambda needs: (approved, budgeted)
        return hq.budget_coverage_errors([(0, sid, 2026, "ZZ02", kind)])

    assert look(hq.COVER_VARIANCE) == {}
    assert 0 in look(hq.COVER_ENTITY)
    assert 0 in look(hq.COVER_VARIANCE, "ZZ_BUD2")


def test_budgeted_entity_with_no_row_reads_zero():
    """An approved Cycle budgets ZZ01, and the account has no row: budget
    zero, never a refusal (22 Sep 2026)."""
    result, queries = read([{}], table_rows=[])
    assert not result.get("errors"), result
    assert result["values"] == [0.0]
    assert len(queries) == 1


def test_an_annual_input_budgets_its_entity():
    result, _ = read([{"entity": "ZZ03"}])
    assert not result.get("errors"), result


def test_consolidated_budget_with_no_approved_cycle_is_refused_naming_the_group():
    result, queries = read([{"fact": "consolidated_budget", "entity": "ZZG",
                             "scenario_id": "ZZ_BUD2"}], cycles=_DRAFT_BUD2)
    assert queries == []
    assert result["errors"] == [no_cycle("ZZ_BUD2", 2026, "group 'ZZG'")]


def test_consolidated_budget_asks_the_scenario_and_year_only():
    """The group holds no Budget Entry of its own: an approved Cycle of the
    year is enough."""
    for scenario_id in ("", "ZZ_BUD"):
        result, _ = read([{"fact": "consolidated_budget", "entity": "ZZG",
                           "scenario_id": scenario_id}])
        assert not result.get("errors"), (scenario_id, result)


def test_budget_coverage_is_looked_up_once_per_batch():
    """Three cells, two entities, two years: one lookup of each doctype for
    the coverage, however many cells."""
    lookups = []
    cycles = CYCLES + [("ZZ-BUD-2027", "ZZ_BUD", 2027, 1)]
    entries = ENTRIES + [("ZZ-BUD-2027", "ZZ01")]
    result, _ = read([{}, {"entity": "ZZ03"}, {"year": 2027},
                      {"fact": "variance_analysis", "measure": "variance_abs"}],
                     cycles=cycles, entries=entries, lookups=lookups)
    assert not result.get("errors"), result
    assert lookups.count("Budget Entry") == 1
    assert lookups.count("Budget Annual Input") == 1
    # one for the active budget scenarios, one for the approved Cycles read
    assert lookups.count("Budget Cycle") <= 2

"""An actuals read of a period with no trial balance is refused (konsol#401).

=K.EPM("E1", 2024, 5, "4000") read 0 although E1's 2024 data is a
single period-end-balance file at P12: "nothing loaded" and "loaded, and the
account is zero" read the same, so a monthly P&L sheet showed eleven
believable zeros.

The rule:

- An actuals read (the Dataset whose scenario_key is ``actuals``, and the
  hierarchy scenario ``actuals``) is refused when the warehouse holds no trial
  balance for the entity in ANY of the periods the cell asks for. A range
  (Q1, FY) with at least one loaded period still reads, as before.
- A wildcard (all-entity) hierarchy read is refused only when no entity in
  the reader's scope has a trial balance for those periods.
- "Loaded" is what ``epm_gold.gold_trial_balance`` holds for (entity,
  fiscal_year, fiscal_period), looked up once per call, not once per row.
- A loaded period where the account has no row still reads 0.
- Budget reads are unchanged: a missing budget is budget zero (decision of
  22 Sep 2026, "Unbudgeted is unfavourable").
- The refusal carries the code ``no_data`` in a parallel ``error_codes``
  list, and the add-in shows it as #N/A rather than #VALUE!.

api.py and hierarchy_query.py run on a stub frappe against a fake ClickHouse
that answers the trial-balance coverage query from ``loaded`` and records
every data query; functions.js runs under node (excel_addin_harness.mjs).
"""
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
APP_DIR = os.path.dirname(HERE)
ROOT = os.path.dirname(APP_DIR)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

_spec = importlib.util.spec_from_file_location(
    "_read_path_stub_401", os.path.join(HERE, "read_path_stub.py"))
read_path_stub = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(read_path_stub)

NO_TB_P5 = "No trial balance for ZZ01 2024 P5"


def _tb_module():
    spec = importlib.util.spec_from_file_location(
        "_host_tb_coverage_401", os.path.join(APP_DIR, "tb_coverage.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _is_coverage(sql):
    return "epm_gold.gold_trial_balance" in sql and "DISTINCT" in sql


def _coverage_reply(loaded, params):
    years = {v for k, v in params.items() if k.startswith("param_cy")}
    return "".join(f"{e}\t{y}\t{p}\n" for e, y, p in sorted(loaded) if str(y) in years)


# ── the rule, without a warehouse ───────────────────────────────────────────

def test_period_label_names_one_period_or_a_range():
    tb = _tb_module()
    assert tb.period_label((5,)) == "P5"
    assert tb.period_label((1, 2, 3)) == "P1-P3"
    assert tb.period_label((3, 1, 2)) == "P1-P3"
    assert tb.period_label((1, 3)) == "P1, P3"


def test_named_entity_is_refused_only_when_no_period_is_loaded():
    tb = _tb_module()
    loaded = {("ZZ01", 2024, 12)}
    assert tb.no_tb_error("ZZ01", 2024, (5,), loaded) == NO_TB_P5
    assert tb.no_tb_error("ZZ01", 2024, (12,), loaded) is None
    # a range with one loaded period reads (an annual file read as FY)
    assert tb.no_tb_error("ZZ01", 2024, tuple(range(1, 13)), loaded) is None
    assert tb.no_tb_error("ZZ01", 2024, (1, 2, 3), loaded) == (
        "No trial balance for ZZ01 2024 P1-P3")
    # another entity's or another year's TB does not count
    assert tb.no_tb_error("ZZ02", 2024, (12,), loaded) == (
        "No trial balance for ZZ02 2024 P12")
    assert tb.no_tb_error("ZZ01", 2023, (12,), loaded) == (
        "No trial balance for ZZ01 2023 P12")


def test_wildcard_is_refused_only_when_no_entity_in_scope_is_loaded():
    tb = _tb_module()
    loaded = {("ZZ01", 2024, 12), ("ZZ02", 2024, 5)}
    # unrestricted scope: any entity's TB counts
    assert tb.no_tb_error_in_scope(2024, (5,), loaded, None) is None
    assert tb.no_tb_error_in_scope(2024, (6,), loaded, None) == (
        "No trial balance for any entity in 2024 P6")
    # restricted scope: only the entities the reader may read count
    assert tb.no_tb_error_in_scope(2024, (5,), loaded, ["ZZ01"]) == (
        "No trial balance for any entity in 2024 P5")
    assert tb.no_tb_error_in_scope(2024, (5,), loaded, ["ZZ01", "ZZ02"]) is None


def test_coverage_query_is_one_parameterised_statement_over_the_years():
    tb = _tb_module()
    sql, params = tb.coverage_query([2024, 2023, 2024])
    assert "SELECT DISTINCT data_area_id, fiscal_year, fiscal_period" in sql
    assert "FROM epm_gold.gold_trial_balance" in sql
    assert sorted(params.values()) == ["2023", "2024"]
    for value in params.values():
        assert value not in sql  # bound, not interpolated
    assert tb.parse_coverage("ZZ01\t2024\t12\nZZ02\t2023\t13\n") == {
        ("ZZ01", 2024, 12), ("ZZ02", 2023, 13)}
    assert tb.parse_coverage("") == set()


# ── the flat read: _batch_query_clickhouse ─────────────────────────────────

_DATASETS = {
    "zz_actuals": dict(scenario_key="actuals", clickhouse_table="epm_gold.zz_actuals",
                       has_scenario_id=0, has_layer=0),
    "zz_budget": dict(scenario_key="budget", clickhouse_table="epm_gold.zz_budget",
                      has_scenario_id=1, has_layer=1),
}


def _run_flat(rows, loaded, reply=""):
    """Returns (result, data queries, coverage queries)."""
    data, coverage = [], []
    fake_frappe = types.ModuleType("frappe")
    fake_frappe.get_all = lambda *a, **k: []
    read_path_stub.install(fake_frappe)
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
            "_host_api_401", os.path.join(APP_DIR, "api.py"))
        api = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(api)

        def get_fact(fact=None, scenario=None):
            d = _DATASETS.get(fact)
            if d is None:
                return None
            return types.SimpleNamespace(
                fact_name=fact, reroute_table=None, reroute_column=None,
                reroute_measure=None, **d)

        api._get_fact = get_fact
        api._get_fact_by_scenario = lambda scenario: None
        api._get_ch_connection = lambda: {}

        def fake_query(sql, params, ch_settings):
            if _is_coverage(sql):
                coverage.append((sql, dict(params)))
                return _coverage_reply(loaded, params)
            data.append((sql, dict(params)))
            return reply

        api._clickhouse_query = fake_query
        reqs = []
        for row in rows:
            req = {"entity": "ZZ01", "year": 2024, "account": "ZZ4000",
                   "measure": "period_net_amount", "periods": (5,),
                   "dimensions": {}, "fact": "zz_actuals"}
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
    return result, data, coverage


def test_flat_actuals_read_of_a_period_with_no_tb_is_refused():
    result, data, coverage = _run_flat(
        [{"periods": (5,)}, {"periods": (12,)}],
        loaded={("ZZ01", 2024, 12)},
        reply="ZZ01\t2024\t12\tZZ4000\t-438.5\n")
    assert result["errors"] == [NO_TB_P5, None]
    assert result["error_codes"] == ["no_data", None]
    assert result["values"] == [None, -438.5]
    assert len(coverage) == 1
    # the refused cell's period is not asked of the data table
    (sql, params), = data
    assert sorted(v for k, v in params.items() if k.startswith("param_fp")) == ["12"]


def test_flat_loaded_period_with_no_row_for_the_account_reads_zero():
    result, _, _ = _run_flat([{"periods": (12,), "account": "ZZ9999"}],
                             loaded={("ZZ01", 2024, 12)})
    assert not result.get("errors"), result
    assert result["values"] == [0.0]


def test_flat_range_with_one_loaded_period_reads():
    result, _, _ = _run_flat(
        [{"periods": tuple(range(1, 13))}],
        loaded={("ZZ01", 2024, 12)},
        reply="ZZ01\t2024\t12\tZZ4000\t-438.5\n")
    assert not result.get("errors"), result
    assert result["values"] == [-438.5]


def test_flat_coverage_is_one_query_per_call_however_many_rows_and_years():
    rows = [{"year": y, "periods": (p,), "account": f"ZZ{a}"}
            for y in (2023, 2024) for p in range(1, 13) for a in range(5)]
    loaded = {("ZZ01", y, p) for y in (2023, 2024) for p in range(1, 13)}
    result, _, coverage = _run_flat(rows, loaded=loaded)
    assert not result.get("errors"), result
    (_, params), = coverage
    assert sorted(params.values()) == ["2023", "2024"]


def test_flat_budget_read_is_not_checked_against_the_tb():
    result, data, coverage = _run_flat(
        [{"fact": "zz_budget", "measure": "period_amount"}], loaded=set())
    assert coverage == []
    assert not result.get("errors"), result
    assert result["values"] == [0.0]
    assert len(data) == 1


def test_flat_coverage_failure_fails_the_actuals_rows_only():
    def run():
        return _run_flat([{"periods": (5,)}, {"fact": "zz_budget", "measure": "period_amount"}],
                         loaded=None)
    # loaded=None makes the fake raise inside the coverage query
    try:
        result, _, _ = run()
    except TypeError:
        raise AssertionError("a failed coverage query escaped the batch")
    assert result["errors"][0] == "ClickHouse query failed"
    assert result["values"][0] is None
    assert result["errors"][1] is None
    assert result["values"][1] == 0.0


# ── the hierarchy read: batch_query_hierarchy ──────────────────────────────

def _run_hierarchy(rows, loaded, allowed=None, reply=""):
    data, coverage = [], []
    spec = importlib.util.spec_from_file_location(
        "_host_hq_401", os.path.join(APP_DIR, "hierarchy_query.py"))
    hq = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(hq)

    def fake_query(sql, params, ch_settings):
        if _is_coverage(sql):
            coverage.append((sql, dict(params)))
            return _coverage_reply(loaded, params)
        data.append((sql, dict(params)))
        return reply

    hq._clickhouse_query = fake_query

    def read_scope(entity, allowed_entities, wildcard=False):
        if wildcard:
            return (None if allowed_entities is None else sorted(allowed_entities)), None
        return None, None

    fake_frappe = types.ModuleType("frappe")
    read_path_stub.install(fake_frappe)
    fake_frappe.log_error = lambda *a, **k: None
    fake_frappe.get_traceback = lambda: ""
    stubs = {
        "frappe": fake_frappe,
        "konsol.clickhouse": types.SimpleNamespace(get_connection=lambda: {}),
        "konsol.entity_permissions": types.SimpleNamespace(entity_read_scope=read_scope),
    }
    saved = {k: sys.modules.get(k) for k in stubs}
    sys.modules.update(stubs)
    try:
        reqs = []
        for row in rows:
            req = {"entity": "ZZ01", "year": 2024, "periods": (5,), "account": "ZZ4000",
                   "scenario": "actuals", "hierarchy_name": "ZZ_H",
                   "hierarchy_node": "ZZ_N", "measure": "period_net_amount"}
            req.update(row)
            reqs.append(req)
        result = hq.batch_query_hierarchy(reqs, allowed_entities=allowed)
    finally:
        for k, mod in saved.items():
            if mod is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = mod
    return result, data, coverage


def test_hierarchy_named_entity_with_no_tb_is_refused():
    result, data, coverage = _run_hierarchy(
        [{"periods": (5,)}, {"periods": (12,)}],
        loaded={("ZZ01", 2024, 12)},
        reply="ZZ01\t2024\tZZ4000\t7.0\n")
    assert result["errors"] == [NO_TB_P5, None]
    assert result["error_codes"] == ["no_data", None]
    assert result["values"] == [None, 7.0]
    assert len(coverage) == 1
    assert len(data) == 1


def test_hierarchy_wildcard_reads_when_any_entity_in_scope_is_loaded():
    result, data, _ = _run_hierarchy(
        [{"entity": "ALL", "periods": (5,)}],
        loaded={("ZZ02", 2024, 5)},
        reply="2024\tZZ4000\t9.0\n")
    assert not result.get("errors"), result
    assert result["values"] == [9.0]


def test_hierarchy_wildcard_is_refused_when_no_entity_in_scope_is_loaded():
    result, data, _ = _run_hierarchy(
        [{"entity": "ALL", "periods": (5,)}],
        loaded={("ZZ02", 2024, 5)}, allowed={"ZZ01"})
    assert result["errors"] == ["No trial balance for any entity in 2024 P5"]
    assert result["error_codes"] == ["no_data"]
    assert data == []


def test_hierarchy_budget_read_is_not_checked_against_the_tb():
    result, _, coverage = _run_hierarchy(
        [{"scenario": "budget", "measure": "period_amount", "scenario_id": ""}],
        loaded=set())
    assert coverage == []
    assert not result.get("errors"), result
    assert result["values"] == [0.0]


# ── epm_batch carries the codes back to the cells that asked ────────────────

def _endpoints():
    spec = importlib.util.spec_from_file_location(
        "_entity_access_host_401", os.path.join(HERE, "test_entity_access_host.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_epm_batch_returns_error_codes_at_the_rows_that_asked():
    host = _endpoints()
    e = host._Endpoints(None)

    def flat_query(reqs):
        return {"values": [None, 1.0], "errors": [NO_TB_P5, None],
                "error_codes": ["no_data", None]}

    def hier_query(reqs, *, allowed_entities):
        return {"values": [None], "errors": ["No trial balance for any entity in 2024 P5"],
                "error_codes": ["no_data"]}

    e.api._batch_query_clickhouse = flat_query
    e.hq.batch_query_hierarchy = hier_query
    out = e.batch([{"entity": "ZZ01", "year": 2024, "period": 5, "account": "1"},
                   {"entity": "ZZ01", "year": 2024, "period": 5, "account": "1",
                    "hierarchy_node": "N"},
                   {"entity": "ZZ01", "year": 2024, "period": 12, "account": "1"}])
    assert out["values"] == [None, None, 1.0]
    assert out["error_codes"] == ["no_data", "no_data", None]
    assert out["errors"][0] == NO_TB_P5


# ── the add-in: no_data is #N/A, any other refusal stays #VALUE! ───────────

def _call_epm(reply):
    node = shutil.which("node")
    assert node, "node is required to run the Excel add-in tests"
    payload = {"fn": "EPM", "args": ["ZZ01", 2024, 5, "ZZ4000"], "reply": reply}
    proc = subprocess.run([node, os.path.join(HERE, "excel_addin_harness.mjs"),
                           json.dumps(payload)], capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def test_addin_shows_a_no_data_refusal_as_not_available():
    out = _call_epm({"message": {"values": [None], "errors": [NO_TB_P5],
                                 "error_codes": ["no_data"]}})
    assert out["error"] == {"code": "#N/A", "message": NO_TB_P5}


def test_addin_keeps_other_refusals_as_invalid_value():
    out = _call_epm({"message": {"values": [None], "errors": ["Invalid year"]}})
    assert out["error"] == {"code": "#VALUE!", "message": "Invalid year"}

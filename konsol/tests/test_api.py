"""TDD tests for konsol EPM API (Frappe proxy to ClickHouse)."""
import ast
import importlib.util
import json
import os
import sys
import types

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
API_PATH = os.path.join(APP_DIR, "api.py")

# Add api module to path for direct import tests
sys.path.insert(0, APP_DIR)


def test_api_file_exists():
    """api.py must exist."""
    assert os.path.exists(API_PATH)


def test_api_has_batch_endpoint():
    """Must have an epm_batch function."""
    with open(API_PATH) as f:
        tree = ast.parse(f.read())
    func_names = [n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]
    assert "epm_batch" in func_names


def test_api_has_health_endpoint():
    """Must have a health check endpoint."""
    with open(API_PATH) as f:
        tree = ast.parse(f.read())
    func_names = [n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]
    assert "health" in func_names


def test_api_is_whitelisted():
    """Endpoints must be whitelisted for guest or session access."""
    with open(API_PATH) as f:
        content = f.read()
    assert "whitelist" in content


def test_api_queries_clickhouse():
    """Must connect to ClickHouse to fetch data."""
    with open(API_PATH) as f:
        content = f.read()
    assert "clickhouse" in content.lower() or "8123" in content


def test_api_returns_values_array():
    """Response must include a values array."""
    with open(API_PATH) as f:
        content = f.read()
    assert "values" in content


def test_api_has_single_value_endpoint():
    """Must have an epm_value function for single cell lookups."""
    with open(API_PATH) as f:
        tree = ast.parse(f.read())
    func_names = [n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]
    assert "epm_value" in func_names


def test_api_has_report_apply_endpoints():
    """Excel add-in Apply report uses list_report_templates and build_cell_map."""
    with open(API_PATH) as f:
        tree = ast.parse(f.read())
    func_names = [n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]
    assert "list_report_templates" in func_names
    assert "build_cell_map" in func_names
    assert "build_snapshot" in func_names


# --- Hardening tests ---

def test_measure_allowlist_exists():
    """Measures are constrained to the fact's allow-list AND the Published registry.

    Data-driven (Dataset.measures ∩ Measure registry), surfaced via
    _get_allowed_measures / _published_measures and enforced by _resolve_and_validate.
    """
    with open(API_PATH) as f:
        content = f.read()
    assert "_get_allowed_measures" in content
    assert "_published_measures" in content


def test_fact_measure_dimension_validated_together():
    """The read path validates fact + measure + dimensions before querying."""
    with open(API_PATH) as f:
        content = f.read()
    assert "_resolve_and_validate" in content


def test_validate_function_exists():
    """A validation function must exist to reject unknown facts/measures/dimensions."""
    with open(API_PATH) as f:
        tree = ast.parse(f.read())
    func_names = [n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]
    assert "_resolve_and_validate" in func_names


def test_validate_measure_raises_on_invalid():
    """_validate_measure must raise/throw for invalid measures."""
    with open(API_PATH) as f:
        content = f.read()
    # Must contain frappe.throw or raise for invalid measures
    assert "frappe.throw" in content or "raise" in content
    assert "Invalid measure" in content


def test_measure_is_regex_validated_before_sql():
    """Measure must be identifier-validated before interpolation into SQL.

    The query measure is interpolated into sum(...), but only after passing the
    _SAFE_IDENTIFIER regex check, which blocks any SQL-injection payload.
    """
    with open(API_PATH) as f:
        content = f.read()
    assert "_SAFE_IDENTIFIER" in content
    assert "_SAFE_IDENTIFIER.match(query_measure)" in content


def test_batch_query_function_exists():
    """_batch_query_clickhouse must exist for grouped queries."""
    with open(API_PATH) as f:
        tree = ast.parse(f.read())
    func_names = [n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]
    assert "_batch_query_clickhouse" in func_names


def test_error_reporting_structure():
    """API must return errors array when errors occur."""
    with open(API_PATH) as f:
        content = f.read()
    assert '"errors"' in content or "'errors'" in content


def test_no_silent_exception_swallowing():
    """Must not silently return 0.0 for all exceptions."""
    with open(API_PATH) as f:
        content = f.read()
    # Old pattern: except Exception: return 0.0
    # Should no longer exist
    lines = content.split("\n")
    for i, line in enumerate(lines):
        if "except Exception" in line and i + 1 < len(lines):
            next_line = lines[i + 1].strip()
            assert next_line != "return 0.0", "Must not silently swallow errors"


def test_trial_balance_net_uses_debit_minus_credit():
    """Trial balance snapshot net must be debit minus credit, not summed net amounts."""
    with open(API_PATH) as f:
        content = f.read()
    assert "_fetch_trial_balance_rows" in content
    assert "sum(period_debit) - sum(period_credit) AS net" in content
    assert "sum(period_net_amount) AS net" not in content


def test_period_net_amount_measure_expression_is_debit_minus_credit():
    """Published period_net_amount measure must derive net from debit and credit."""
    from konsol.tests.shipped import shipped  # konsol#230: now in defaults/
    records = shipped("measure.json")
    period_net = next(
        r for r in records if r.get("measure_name") == "period_net_amount"
    )
    assert period_net["expression"] == "sum(debit_amount) - sum(credit_amount)"


# ---------------------------------------------------------------------------
# konsol#231: one query per group, not one per period
#
# _batch_query_clickhouse carried each request's `periods` tuple in its
# grouping key, so cells that differed only in period could never share a
# query: a five-year monthly sheet became 5 x 12 = 60 round trips of ~29 ms.
# The SQL already emitted `fiscal_period IN (...)`; the only reason periods
# keyed the group was that fiscal_period was missing from select_cols, so a
# returned row could not be attributed to the cell that asked for it. With
# fiscal_period selected and grouped, one query answers the whole row and each
# cell takes the sum of the periods it asked for (FY sums 1-12).
#
# api.py is loaded under a private name with a stub frappe and a fake
# _clickhouse_query that records every (sql, params).
# ---------------------------------------------------------------------------

def _tsv(*rows):
    """ClickHouse TabSeparated reply: the select columns, then the value."""
    return "".join("\t".join(str(col) for col in row) + "\n" for row in rows)


def _run_batch(rows, reply=""):
    """api._batch_query_clickhouse over ``rows`` (dicts overriding a default
    zz_actuals request), against a fake ClickHouse answering ``reply``.

    Returns (result, [(sql, params), ...]).
    """
    queries = []
    zz_actuals = types.SimpleNamespace(
        fact_name="zz_actuals", scenario_key="actuals",
        clickhouse_table="epm_gold.zz_actuals", has_scenario_id=0, has_layer=0,
        reroute_table=None, reroute_column=None, reroute_measure=None,
    )

    fake_frappe = types.ModuleType("frappe")
    fake_frappe.get_all = lambda *a, **k: []
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
        spec = importlib.util.spec_from_file_location("_host_api_k231", API_PATH)
        api = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(api)

        api._get_fact = (
            lambda fact=None, scenario=None: zz_actuals if fact == "zz_actuals" else None)
        api._get_fact_by_scenario = lambda scenario: None
        api._get_ch_connection = lambda: {}

        def fake_query(sql, params, ch_settings):
            queries.append((sql, dict(params)))
            return reply

        api._clickhouse_query = fake_query

        reqs = []
        for row in rows:
            req = {
                "entity": "ZZ01", "year": 2026, "account": "ZZ100",
                "measure": "period_net_amount", "periods": (1,),
                "dimensions": {}, "fact": "zz_actuals",
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
    return result, queries


def test_two_periods_of_one_cell_row_make_one_query():
    """January and February of the same cell row share a single query."""
    result, queries = _run_batch(
        [{"periods": (1,)}, {"periods": (2,)}],
        reply=_tsv(
            ("ZZ01", "2026", "1", "ZZ100", "100.0"),
            ("ZZ01", "2026", "2", "ZZ100", "250.0"),
        ))
    assert not result.get("errors"), result
    assert len(queries) == 1, (
        "konsol#231: cells differing only in period must share one query; "
        f"got {len(queries)}")
    (sql, params), = queries
    # both periods travel in the one IN list
    assert sorted(v for k, v in params.items() if k.startswith("param_fp")) == ["1", "2"]
    # fiscal_period is selected and grouped, so a row can be attributed back
    select_cols = sql.split("SELECT ")[1].split(", coalesce")[0]
    assert "fiscal_period" in select_cols
    assert "fiscal_period" in sql.split("GROUP BY ")[1]
    # each cell still reads its own period, not the group's total
    assert result["values"] == [100.0, 250.0]


def test_a_full_year_cell_sums_its_twelve_periods():
    """A cell asking FY (periods 1-12) gets the sum of the twelve rows."""
    result, queries = _run_batch(
        [{"periods": tuple(range(1, 13))}],
        reply=_tsv(*[("ZZ01", "2026", str(p), "ZZ100", "10.0") for p in range(1, 13)]))
    assert not result.get("errors"), result
    assert len(queries) == 1
    assert result["values"] == [120.0]


def test_a_cell_whose_period_has_no_row_reads_zero():
    """A period the warehouse has no row for contributes 0.0, not the group's."""
    result, _ = _run_batch(
        [{"periods": (1,)}, {"periods": (2,)}],
        reply=_tsv(("ZZ01", "2026", "1", "ZZ100", "100.0")))
    assert not result.get("errors"), result
    assert result["values"] == [100.0, 0.0]


def test_cells_that_differ_in_measure_or_dimensions_still_query_separately():
    """Only the period left the grouping key: measure and dimension shape stay."""
    _, by_measure = _run_batch([
        {"measure": "period_net_amount"},
        {"measure": "period_debit"},
    ])
    assert len(by_measure) == 2

    _, by_dims = _run_batch([
        {"dimensions": {}},
        {"dimensions": {"cost_center": "ZZ-CC1"}},
    ])
    assert len(by_dims) == 2




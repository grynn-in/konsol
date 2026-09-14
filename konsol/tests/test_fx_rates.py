"""Read-only FX-rates endpoint (konsolidat#91 Part B; konsol#103, #175).

One source of truth for FX rates (decided 13 Sep 2026): the endpoint returns
the governed rates konsol publishes, not the ERP feed. The function is run
here against a stub ClickHouse, and its wiring is asserted in source."""
import ast
import os
import sys
import types

API = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "api.py")


def _src():
    with open(API) as f:
        return f.read()


def _fx_rates(execute):
    """api.fx_rates and the constants it reads, compiled alone against stubs."""
    tree = ast.parse(_src())
    keep = [n for n in tree.body
            if (isinstance(n, ast.FunctionDef) and n.name == "fx_rates")
            or (isinstance(n, ast.Assign) and getattr(n.targets[0], "id", "") in
                ("GOVERNED_FX_TABLE", "FISCAL_PERIODS_TABLE", "FISCAL_PERIODS_DEDUP_SQL"))]
    ns = {"frappe": types.SimpleNamespace(whitelist=lambda *a, **k: (lambda fn: fn))}
    exec(compile(ast.Module(body=keep, type_ignores=[]), API, "exec"), ns)
    ch = types.ModuleType("konsol.clickhouse")
    ch.execute = execute
    fn = ns["fx_rates"]

    def call(**kw):
        saved = {n: sys.modules.get(n) for n in ("konsol", "konsol.clickhouse")}
        sys.modules["konsol.clickhouse"] = ch
        sys.modules.setdefault("konsol", types.ModuleType("konsol"))
        try:
            return fn(**kw)
        finally:
            for n, old in saved.items():
                if old is None:
                    sys.modules.pop(n, None)
                else:
                    sys.modules[n] = old
    return call


def test_fx_rates_defined_and_whitelisted():
    src = _src()
    assert "def fx_rates(" in src
    # whitelisted; login required (NOT allow_guest — FX data is internal)
    tree = ast.parse(src)
    fn = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "fx_rates")
    decs = [ast.unparse(d) for d in fn.decorator_list]
    assert any("whitelist" in d for d in decs)
    assert not any("allow_guest" in d for d in decs)


def test_fx_rates_is_read_only_and_reads_the_governed_rates():
    src = _src().split("def fx_rates(")[1].split("\n@frappe.whitelist")[0]
    assert "GOVERNED_FX_TABLE" in src and "silver_exchange_rates" not in src
    for verb in ("INSERT", "TRUNCATE", "ALTER", "DELETE", "DROP"):
        assert verb not in src
    # limit is integer-cast + bounded
    assert "int(limit)" in src and "min(" in src


def test_fx_rates_period_start_from_rows():
    """konsol#189: period_start/as_of come from the declared calendar row
    (epm_staging.fiscal_periods.start_date), never guessed month arithmetic."""
    calls = []
    fx_rates = _fx_rates(lambda sql, params=None: calls.append((sql, params)) or
                         '{"data": []}')
    fx_rates(as_of="2099-12-31")
    [(sql, params)] = calls
    assert "toString(fp.start_date) AS period_start" in sql
    assert "fp.start_date <= {asof:Date}" in sql
    assert "makeDate" not in sql
    assert "least(" not in sql
    assert "greatest(" not in sql
    # konsol#189 row 70f: fiscal_periods is truncate+insert resynced, so two
    # concurrent resyncs can leave a period twice; join a one-row-per-period
    # subquery, never the raw table, so a rate is never doubled.
    assert "GROUP BY fiscal_year, fiscal_period" in sql
    assert "min(start_date) AS start_date" in sql
    assert "FROM epm_staging.fiscal_periods" in sql
    assert ") AS fp" in sql


def test_filters_are_bound_as_param_prefixed_http_parameters():
    """#175: ClickHouse reads a bare query-string name as a SETTING, so every
    filtered call failed with UNKNOWN_SETTING. Values go as param_<name>."""
    calls = []
    fx_rates = _fx_rates(lambda sql, params=None: calls.append((sql, params)) or
                         '{"data": [{"from_currency": "JPY", "rate": 0.006607}]}')
    out = fx_rates(from_currency="jpy", to_currency="USD", rate_type="Closing", fiscal_year="2099",
                   fiscal_period=12, as_of="2099-12-31", limit="9999")
    [(sql, params)] = calls
    assert params == {"param_fc": "JPY", "param_tc": "USD", "param_rt": "Closing", "param_fy": 2099,
                      "param_fp": 12, "param_asof": "2099-12-31"}
    for bound in ("{fc:String}", "{tc:String}", "{rt:String}", "{fy:UInt16}", "{fp:UInt8}", "{asof:Date}"):
        assert bound in sql, bound
    assert ("FROM epm_staging.group_exchange_rates INNER JOIN (SELECT fiscal_year, "
            "fiscal_period, min(start_date) AS start_date FROM epm_staging.fiscal_periods "
            "GROUP BY fiscal_year, fiscal_period) AS fp USING (fiscal_year, fiscal_period) "
            "WHERE" in sql)
    assert "LIMIT 5000 " in sql
    assert " rate, document " in sql, "the true rate, as published"
    assert "silver_exchange_rates" not in sql and "exchange_rate_type" not in sql, "never the ERP feed"
    assert out == {"rows": [{"from_currency": "JPY", "rate": 0.006607}], "count": 1}
    calls.clear()
    fx_rates()
    assert calls[0][1] == {} and " WHERE " not in calls[0][0]


def test_fx_rates_uses_execute_helper():
    src = _src()
    assert "from konsol.clickhouse import execute" in src

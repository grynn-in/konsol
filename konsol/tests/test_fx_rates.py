"""Read-only FX-rates endpoint (konsolidat#91 Part B). Static-assertion style —
the endpoint hits ClickHouse, so we assert the wiring/safety in source."""
import ast
import os

API = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "api.py")


def _src():
    with open(API) as f:
        return f.read()


def test_fx_rates_defined_and_whitelisted():
    src = _src()
    assert "def fx_rates(" in src
    # whitelisted; login required (NOT allow_guest — FX data is internal)
    tree = ast.parse(src)
    fn = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "fx_rates")
    decs = [ast.unparse(d) for d in fn.decorator_list]
    assert any("whitelist" in d for d in decs)
    assert not any("allow_guest" in d for d in decs)


def test_fx_rates_is_read_only_and_parameterized():
    src = _src().split("def fx_rates(")[1]
    # read-only: SELECT from the silver rates view, no write verbs
    assert "silver_exchange_rates" in src
    for verb in ("INSERT", "TRUNCATE", "ALTER", "DELETE", "DROP"):
        assert verb not in src
    # filters bound as CH params (injection-safe), not f-string interpolated values
    assert "{fc:String}" in src and "{rt:String}" in src
    # limit is integer-cast + bounded
    assert "int(limit)" in src and "min(" in src


def test_fx_rates_uses_execute_helper():
    src = _src()
    assert "from konsol.clickhouse import execute" in src

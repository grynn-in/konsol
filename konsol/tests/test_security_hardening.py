"""Tests for security hardening fixes.

Covers:
  - #8  table-name validation before SQL interpolation
  - #9  entity-level authorization on epm_value / epm_batch
  - #10 constant-time webhook secret comparison
  - #11 ClickHouse HTTPS / TLS support

These are pure-function and source-inspection tests — no live site, no
ClickHouse, and (deliberately) no monkeypatching. The security *policy* lives
in side-effect-free helpers that take plain arguments, so it can be asserted
directly; the thin Frappe wiring around it is checked by source inspection,
matching the style used elsewhere in this test suite.
"""
import ast
import os

from konsol import api
from konsol import clickhouse


APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _api_src():
    with open(os.path.join(APP_DIR, "api.py")) as f:
        return f.read()


def _clickhouse_src():
    with open(os.path.join(APP_DIR, "clickhouse.py")) as f:
        return f.read()


# ---------------------------------------------------------------------------
# #11 ClickHouse TLS  (pure connection_url + source guarantees)
# ---------------------------------------------------------------------------

def test_connection_url_defaults_to_http():
    conn = {"host": "localhost", "port": "8123", "secure": False}
    assert clickhouse.connection_url(conn) == "http://localhost:8123/"


def test_connection_url_uses_https_when_secure():
    conn = {"host": "ch.example.com", "port": "8443", "secure": True}
    assert clickhouse.connection_url(conn) == "https://ch.example.com:8443/"


def test_both_query_paths_pass_tls_verify():
    """Both ClickHouse callers must forward verify= to requests."""
    assert "verify=conn.get(\"verify\"" in _clickhouse_src()
    assert "verify=ch_settings.get(\"verify\"" in _api_src()


def test_query_paths_use_connection_url_not_hardcoded_http():
    # No raw f"http://... URL building left in the query paths.
    assert 'f"http://{ch_settings' not in _api_src()
    assert 'f"http://{conn' not in _clickhouse_src()


# ---------------------------------------------------------------------------
# #8 table-name validation  (pure regex)
# ---------------------------------------------------------------------------

def test_safe_table_name_accepts_qualified_name():
    assert api._SAFE_TABLE_NAME.match("epm_gold.budget_monthly_input")


def test_safe_table_name_blocks_injection():
    assert not api._SAFE_TABLE_NAME.match("gold.facts; DROP TABLE x --")
    assert not api._SAFE_TABLE_NAME.match("gold.facts WHERE 1=1")
    assert not api._SAFE_TABLE_NAME.match("facts")          # must be schema-qualified
    assert not api._SAFE_TABLE_NAME.match("gold.facts UNION SELECT 1")


def test_batch_query_validates_table_before_sql():
    """The FROM clause must be guarded by _SAFE_TABLE_NAME, before query exec."""
    src = _api_src()
    body = src.split("def _batch_query_clickhouse")[1].split("\ndef ")[0]
    # Table is validated and the bad-table branch sets an error + skips the row.
    assert "_SAFE_TABLE_NAME.match(table" in body
    assert "Invalid table identifier" in body
    # Validation must appear before the SQL string is assembled / executed.
    assert body.index("_SAFE_TABLE_NAME.match(table") < body.index("_clickhouse_query(")


# ---------------------------------------------------------------------------
# #9 entity-level authorization  (pure policy function)
# ---------------------------------------------------------------------------

def test_unrestricted_when_no_perm_doctype():
    assert api._resolve_allowed_entities("alice", [], "", {}) is None


def test_unrestricted_for_system_manager():
    assert api._resolve_allowed_entities(
        "alice", ["System Manager"], "Legal Entity",
        {"Legal Entity": [{"doc": "E1"}]}) is None


def test_unrestricted_for_administrator():
    assert api._resolve_allowed_entities("Administrator", [], "Legal Entity", {}) is None


def test_allow_list_from_user_permissions():
    allowed = api._resolve_allowed_entities(
        "alice", ["EPM User"], "Legal Entity",
        {"Legal Entity": [{"doc": "E1"}, {"doc": "E2"}]})
    assert allowed == {"E1", "E2"}


def test_empty_allow_list_when_perms_configured_but_none_granted():
    # Configured doctype but no grants → user sees no entities (deny-by-default).
    allowed = api._resolve_allowed_entities("alice", ["EPM User"], "Legal Entity", {})
    assert allowed == set()


# Every ClickHouse read decides entity access through
# entity_permissions.entity_read_scope (konsol#158), directly or through
# api._assert_entity_access -> entity_permissions.assert_entity_access. The rule
# itself is exercised in test_entity_access_host.py; these check the wiring.
_READ_GATES = {"_assert_entity_access", "entity_read_scope"}
_CH_READS = {"_batch_query_clickhouse", "batch_query_hierarchy",
             "_fetch_trial_balance_rows", "compile_cell_map"}


def _src(rel):
    with open(os.path.join(APP_DIR, rel)) as f:
        return f.read()


def _calls(nodes):
    """Names called anywhere in these AST nodes (f(), x.f())."""
    names = set()
    for top in nodes:
        for n in ast.walk(top):
            if isinstance(n, ast.Call):
                f = n.func
                if isinstance(f, ast.Name):
                    names.add(f.id)
                elif isinstance(f, ast.Attribute):
                    names.add(f.attr)
    return names


def _def(tree, name):
    fn = next((n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name), None)
    assert fn is not None, f"no top-level def {name}"
    return fn


def _branch(stmts, test_src):
    """The `if <test_src>:` statement among ``stmts``."""
    node = next((s for s in stmts if isinstance(s, ast.If) and ast.unparse(s.test) == test_src), None)
    assert node is not None, f"no `if {test_src}:` block"
    return node


def test_every_clickhouse_read_endpoint_calls_the_entity_helper():
    tree = ast.parse(_api_src())
    readers = {
        fn.name: fn for fn in tree.body
        if isinstance(fn, ast.FunctionDef)
        and any("whitelist" in ast.unparse(d) for d in fn.decorator_list)
        and _calls([fn]) & _CH_READS
    }
    # Not vacuous: the known readers are found.
    assert {"epm_value", "epm_batch", "build_cell_map", "build_snapshot"} <= set(readers)
    for name, fn in readers.items():
        assert _calls([fn]) & _READ_GATES, \
            f"{name} reads ClickHouse without entity_read_scope / _assert_entity_access"


def test_both_modes_of_k_epm_call_the_entity_helper():
    tree = ast.parse(_api_src())

    value = _def(tree, "epm_value")
    hier = _branch(value.body, "node_code")
    assert "entity_read_scope" in _calls(hier.body)
    assert "_assert_entity_access" in _calls([s for s in value.body if s is not hier])

    batch = _def(tree, "epm_batch")
    loop = next(s for s in batch.body if isinstance(s, ast.For))
    hier = _branch(loop.body, "_is_hierarchy_mode(req)")
    assert "entity_read_scope" in _calls(hier.body)
    assert "entity_read_scope" in _calls([s for s in loop.body if s is not hier])
    # The allow-list is resolved once for the batch, not once per row.
    assert "_allowed_entities" in _calls([s for s in batch.body if s is not loop])
    assert "_allowed_entities" not in _calls([loop])


def test_hierarchy_reads_carry_the_allow_list_to_the_helper():
    api_tree = ast.parse(_api_src())
    calls = [n for n in ast.walk(api_tree) if isinstance(n, ast.Call)
             and getattr(n.func, "id", None) == "batch_query_hierarchy"]
    assert len(calls) == 2
    for c in calls:
        kw = {k.arg: ast.unparse(k.value) for k in c.keywords}
        assert kw.get("allowed_entities") == "allowed_entities", ast.unparse(c)
    hq = ast.parse(_src("hierarchy_query.py"))
    assert "entity_read_scope" in _calls([_def(hq, "batch_query_hierarchy")])
    ep = ast.parse(_src("entity_permissions.py"))
    assert "entity_read_scope" in _calls([_def(ep, "assert_entity_access")])


def test_no_inline_entity_checks_left():
    # The fourth way must not creep back: no `x in allowed_entities` or
    # `x in allowed_entity_codes()` in the read paths.
    for rel in ("api.py", "hierarchy_query.py"):
        for n in ast.walk(ast.parse(_src(rel))):
            if not isinstance(n, ast.Compare):
                continue
            if not any(isinstance(op, (ast.In, ast.NotIn)) for op in n.ops):
                continue
            for right in n.comparators:
                text = ast.unparse(right)
                assert text != "allowed_entities" and "allowed_entity_codes" not in text, \
                    f"{rel}: inline entity check `{ast.unparse(n)}`; use entity_read_scope"


_GATE = "_assert_entity_access"
_GATE_WHY = (f"api.{_GATE} changed or was rebound. It is the entity-access gate for "
             "ClickHouse reads (the check itself is tested in test_entity_access_host.py). "
             "Get a security review, then update this tripwire.")


def _binds_gate(node):
    """Does this AST node bind, rebind or name the gate anywhere in api.py?"""
    match_nodes = tuple(getattr(ast, n) for n in ("MatchAs", "MatchStar") if hasattr(ast, n))
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return node.name == _GATE
    if isinstance(node, (ast.Import, ast.ImportFrom)):
        return any(a.name == "*" or (a.asname or a.name.split(".")[0]) == _GATE
                   for a in node.names)
    if isinstance(node, ast.Name):
        return node.id == _GATE and not isinstance(node.ctx, ast.Load)
    if isinstance(node, (ast.Global, ast.Nonlocal)):
        return _GATE in node.names
    if match_nodes and isinstance(node, match_nodes):
        return node.name == _GATE
    if isinstance(node, ast.Constant):  # globals()[...] / setattr(module, ...)
        return isinstance(node.value, str) and node.value == _GATE
    if isinstance(node, ast.Attribute):  # __code__ swaps, patching the shared check
        return not isinstance(node.ctx, ast.Load) and node.attr in (
            "__code__", _GATE, "assert_entity_access", "entity_read_scope")
    return False


def test_assert_entity_access_raises_permission_error_source():
    # A tripwire, not a parser of intent. api._assert_entity_access must be
    # exactly "import the shared check, call it with entity", bound once in the
    # whole file, with no decorator. Wrapping the call, adding a user argument,
    # blanking entity, a yield, or a later rebind anywhere in api.py (a def in a
    # block, unpacking, globals()/setattr, a __code__ swap) all fail here.
    # Limits: a sys.modules swap or exec() of a built string cannot be seen
    # statically; test_assert_entity_access_is_the_function_in_the_source checks
    # the loaded function instead.
    tree = ast.parse(_api_src())
    n = sum(_binds_gate(node) for node in ast.walk(tree))
    assert n == 1, f"expected exactly 1 binding of {_GATE} in api.py, found {n}. {_GATE_WHY}"
    fn = next((node for node in tree.body
               if isinstance(node, ast.FunctionDef) and node.name == _GATE), None)
    assert fn is not None, f"{_GATE} is not a plain top-level def. {_GATE_WHY}"
    assert not fn.decorator_list, f"{_GATE} has a decorator. {_GATE_WHY}"
    assert ast.unparse(fn.args) == "entity", \
        f"{_GATE} signature is ({ast.unparse(fn.args)}), expected (entity). {_GATE_WHY}"
    body = [ast.unparse(node) for node in fn.body
            if not (isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant))]
    assert body == [
        "from konsol.entity_permissions import assert_entity_access",
        "assert_entity_access(entity)",
    ], f"{_GATE} body is {body}. {_GATE_WHY}"


def test_assert_entity_access_is_the_function_in_the_source():
    # Catches a rebind that happens while api.py imports (anything the static
    # tripwire cannot see): the loaded gate must be the def in api.py.
    fn = next((node for node in ast.parse(_api_src()).body
               if isinstance(node, ast.FunctionDef) and node.name == _GATE), None)
    assert fn is not None, f"{_GATE} is not a plain top-level def. {_GATE_WHY}"
    code = getattr(api, _GATE).__code__
    expected = os.path.realpath(os.path.join(APP_DIR, "api.py"))
    assert os.path.realpath(code.co_filename) == expected, (
        f"{_GATE} was loaded from {code.co_filename}, expected {expected} "
        f"(a different checkout on sys.path, or a rebind). {_GATE_WHY}")
    assert code.co_firstlineno == fn.lineno, (
        f"the loaded {_GATE} starts at line {code.co_firstlineno}, the def in "
        f"api.py at line {fn.lineno}. {_GATE_WHY}")


# ---------------------------------------------------------------------------
# #10 constant-time webhook comparison  (source guarantee)
# ---------------------------------------------------------------------------

def test_webhook_uses_constant_time_compare():
    src = _api_src()
    assert "hmac.compare_digest" in src
    assert "secret != expected" not in src

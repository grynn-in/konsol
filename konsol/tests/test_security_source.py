"""Source-only security checks for entity access on ClickHouse reads (konsol#158).

Moved out of test_security_hardening.py, which imports konsol.api and so needs
real frappe: the host runner and CI skip that whole file, so these checks never
ran there. They read api.py and its neighbours as text and parse them with ast,
so this file imports neither frappe nor konsol.api (see the last test).

The live check that the loaded api._assert_entity_access is the def in api.py
(test_assert_entity_access_is_the_function_in_the_source) stays in
test_security_hardening.py: it must import konsol.api from sys.path to catch a
different checkout or an import-time rebind, and loading api.py from a known
path under a stub frappe would make its file check pass by construction.
"""
import ast
import os

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _api_src():
    with open(os.path.join(APP_DIR, "api.py")) as f:
        return f.read()


# Every ClickHouse read decides entity access through
# entity_permissions.entity_read_scope (konsol#158), directly or through
# api._assert_entity_access -> entity_permissions.assert_entity_access. The rule
# itself is exercised in test_entity_access_host.py; these check the wiring.
_READ_GATES = {"_assert_entity_access", "entity_read_scope"}
_CH_READS = {"_batch_query_clickhouse", "batch_query_hierarchy",
             "_fetch_trial_balance_rows", "compile_cell_map",
             "_clickhouse_query", "execute"}
#: Whitelisted ClickHouse readers that need no entity scope. A new reader must
#: either call the helper or be named here, on purpose, with its reason.
_GROUP_LEVEL_READERS = {
    "fx_rates": "epm_silver.silver_exchange_rates: group-wide currency rates, no entity column",
}


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
    stale = set(_GROUP_LEVEL_READERS) - set(readers)
    assert not stale, f"{sorted(stale)} no longer read ClickHouse; drop them from _GROUP_LEVEL_READERS"
    for name, fn in readers.items():
        if name in _GROUP_LEVEL_READERS:
            continue
        assert _calls([fn]) & _READ_GATES, (
            f"{name} reads ClickHouse ({', '.join(sorted(_calls([fn]) & _CH_READS))}) without "
            "entity_read_scope / _assert_entity_access. Gate it, or name it in "
            "_GROUP_LEVEL_READERS with the reason it needs no entity scope.")


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


_HELPER = "entity_read_scope"


def _helper_bindings(tree):
    """Every binding of the name entity_read_scope in this tree other than a
    plain `from konsol.entity_permissions import entity_read_scope`."""
    match_nodes = tuple(getattr(ast, n) for n in ("MatchAs", "MatchStar") if hasattr(ast, n))
    found = []
    for n in ast.walk(tree):
        what = None
        if isinstance(n, (ast.Import, ast.ImportFrom)):
            for a in n.names:
                plain = (isinstance(n, ast.ImportFrom) and n.level == 0
                         and n.module == "konsol.entity_permissions"
                         and a.name == _HELPER and a.asname is None)
                if plain:
                    continue
                if a.name == "*" or _HELPER in (a.name, a.asname, a.name.split(".")[0]):
                    what = f"import `{ast.unparse(n)}`"
        elif isinstance(n, ast.Name) and n.id == _HELPER and not isinstance(n.ctx, ast.Load):
            what = f"assignment to `{_HELPER}`"
        elif isinstance(n, ast.Attribute) and n.attr == _HELPER and not isinstance(n.ctx, ast.Load):
            what = f"attribute assignment `{ast.unparse(n)}`"
        elif isinstance(n, (ast.Global, ast.Nonlocal)) and _HELPER in n.names:
            what = f"`{ast.unparse(n)}`"
        elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and n.name == _HELPER:
            what = f"def/class named `{_HELPER}`"
        elif isinstance(n, ast.arg) and n.arg == _HELPER:
            what = f"parameter named `{_HELPER}`"
        elif isinstance(n, ast.ExceptHandler) and n.name == _HELPER:
            what = f"`except ... as {_HELPER}`"
        elif match_nodes and isinstance(n, match_nodes) and n.name == _HELPER:
            what = f"match capture `{_HELPER}`"
        elif isinstance(n, ast.Constant) and n.value == _HELPER:
            what = f"string constant '{_HELPER}' (the globals()/setattr route)"
        if what:
            found.append(f"line {getattr(n, 'lineno', '?')}: {what}")
    return found


def test_entity_read_scope_is_only_imported_plainly_in_api():
    # A tripwire like the one for _assert_entity_access below: rebinding the
    # rule inside api.py (a local lambda after the import, an alias, a
    # global, globals()/setattr) switches the named-entity check off while
    # every call to it still looks right.
    tree = ast.parse(_api_src())
    found = _helper_bindings(tree)
    assert not found, (
        f"api.py binds {_HELPER} other than by a plain "
        f"`from konsol.entity_permissions import {_HELPER}`: " + "; ".join(found)
        + ". It is the entity-access rule for ClickHouse reads; get a security review.")
    imports = [n for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)
               and any(a.name == _HELPER for a in n.names)]
    assert len(imports) >= 2, "epm_value and epm_batch each import entity_read_scope"


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


def test_this_file_runs_without_frappe():
    # The point of this file: CI has no frappe. A module-level import of frappe
    # or konsol.api would turn every check above into a silent skip.
    with open(os.path.abspath(__file__)) as f:
        tree = ast.parse(f.read())
    for node in tree.body:
        if isinstance(node, ast.Import):
            mods = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            mods = [f"{node.module}.{a.name}" for a in node.names]
        else:
            continue
        for m in mods:
            assert m.split(".")[0] != "frappe" and not m.startswith("konsol.api"), \
                f"module-level import `{ast.unparse(node)}` needs frappe; CI would skip this file"

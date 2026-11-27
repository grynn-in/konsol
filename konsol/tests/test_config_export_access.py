"""Who may read the whole configuration as one bundle (konsol#472).

`export_config_api` and `diff_config_api` in `konsol/cli_api.py` return (or
compare a bundle against) every Dimension, Measure, Dataset and Connector on
the site. `export_config` builds its rows with `frappe.get_doc`, which checks
no permission, and the Dimension, Measure and Dataset doctypes grant read to
System Manager and EPM Admin only. So both reads are gated the way the writes
already are: their first real statement is `check_epm_admin()` from
`konsol.schema_lifecycle`, the guard the Dimension, Measure and Dataset
publish methods open with. They stay GET (they write nothing);
`test_cli_api_slim.py` pins that.

Source-level, in the `test_assertion_run_access.py` style: the module is
parsed with `ast`, so no frappe and no site are needed. The guard itself is
compiled against a stub frappe and exercised, not grepped.
"""
import ast
import os
import types

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLI_API = os.path.join(APP_DIR, "cli_api.py")
SCHEMA_LIFECYCLE = os.path.join(APP_DIR, "schema_lifecycle.py")
GATED_READS = ("export_config_api", "diff_config_api")
GUARD = "check_epm_admin"


def _tree(path):
    with open(path) as f:
        return ast.parse(f.read())


def _function(tree, name):
    fn = next((n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name), None)
    assert fn is not None, f"no top-level function {name}"
    return fn


def _first_statement(fn):
    """The first real statement of the body — the docstring and any lazy
    imports are not statements a caller can be refused by."""
    body = fn.body
    if (body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)):
        body = body[1:]
    while body and isinstance(body[0], (ast.Import, ast.ImportFrom)):
        body = body[1:]
    assert body, f"{fn.name} has no body"
    return body[0]


def _is_bare_guard_call(stmt):
    """`check_epm_admin()` — a plain call of the imported name, no arguments."""
    return (isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call)
            and isinstance(stmt.value.func, ast.Name) and stmt.value.func.id == GUARD
            and not stmt.value.args and not stmt.value.keywords)


def _imports_the_guard(tree, fn):
    """`from konsol.schema_lifecycle import check_epm_admin`, at module level
    or lazily inside the function: a local function of the same name could be
    a no-op, so the name must come from the shared guard."""
    nodes = list(tree.body) + list(fn.body)
    return any(isinstance(n, ast.ImportFrom) and n.module == "konsol.schema_lifecycle"
               and any(a.name == GUARD and a.asname is None for a in n.names) for n in nodes)


def test_the_config_reads_open_with_check_epm_admin():
    """The gate is the first statement, ahead of parsing the spec, so a user
    without the role is refused whatever they send."""
    tree = _tree(CLI_API)
    bad = []
    for name in GATED_READS:
        fn = _function(tree, name)
        stmt = _first_statement(fn)
        if not _is_bare_guard_call(stmt):
            bad.append(f"{name} does not open with {GUARD}(): {ast.unparse(stmt)}")
        elif not _imports_the_guard(tree, fn):
            bad.append(f"{name} calls {GUARD}() but cli_api does not import it from konsol.schema_lifecycle")
    assert not bad, "\n".join(bad)


def _compiled_guard():
    """check_epm_admin and the role set it reads, compiled from
    schema_lifecycle.py against a stub frappe whose roles the test sets."""
    tree = _tree(SCHEMA_LIFECYCLE)
    consts = [n for n in tree.body if isinstance(n, ast.Assign)
              and any(isinstance(t, ast.Name) and t.id == "_ALLOWED_ROLES" for t in n.targets)]
    assert consts, "schema_lifecycle has no _ALLOWED_ROLES"
    fn = _function(tree, GUARD)
    module = ast.Module(body=consts + [fn], type_ignores=[])

    class Refused(Exception):
        pass

    def throw(msg, exc=None):
        raise Refused(msg)

    stub = types.SimpleNamespace(get_roles=lambda: [], throw=throw, PermissionError=Refused)
    ns = {"frappe": stub}
    exec(compile(ast.fix_missing_locations(module), SCHEMA_LIFECYCLE, "exec"), ns)
    return ns[GUARD], stub, Refused


def test_the_guard_refuses_every_role_but_the_admin_ones():
    """The guard the reads open with really refuses: an EPM Analyst and an EPM
    User (who may open Connector but not Dimension, Measure or Dataset) are
    thrown out; EPM Admin and System Manager pass."""
    guard, stub, Refused = _compiled_guard()
    for roles in (["Guest"], ["EPM Analyst"], ["EPM User"], ["EPM Analyst", "EPM User"], []):
        stub.get_roles = lambda roles=roles: roles
        try:
            guard()
            raise AssertionError(f"{GUARD} let {roles} through")
        except Refused:
            pass
    for roles in (["EPM Admin"], ["System Manager"], ["EPM User", "EPM Admin"]):
        stub.get_roles = lambda roles=roles: roles
        guard()

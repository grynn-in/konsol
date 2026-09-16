"""Who may start a close run, and who may see the launch form's options
(konsol#166).

`trigger_close_run` writes: it inserts an Assertion Run, commits, and enqueues
the assertion suite on the long queue. So it is POST-only, and gated to the
roles that run the close — Close Lead (`EPM Admin`) and System Manager.

`launch_options` writes nothing, so it stays a GET, but it reads with
`frappe.get_all`, which ignores permissions: it must be gated to the roles that
launch pipelines — `EPM Admin`, `EPM Analyst` and System Manager.

Source-level, in the `test_role_access.py` style: the file is parsed with
`ast`, so no frappe and no site are needed.
"""
import ast
import os

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ASSERTION_RUN = os.path.join(APP_DIR, "consolidation", "doctype", "assertion_run", "assertion_run.py")
ORCHESTRATOR_API = os.path.join(APP_DIR, "orchestrator", "api.py")


def _function(path, name):
    with open(path) as f:
        tree = ast.parse(f.read())
    fn = next((n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name), None)
    assert fn is not None, f"{os.path.relpath(path, APP_DIR)} has no {name}"
    return fn


def _whitelist_methods(fn):
    """The `methods=` of the function's frappe.whitelist decorator, or None."""
    for dec in fn.decorator_list:
        if (isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute)
                and dec.func.attr == "whitelist"
                and isinstance(dec.func.value, ast.Name) and dec.func.value.id == "frappe"):
            for kw in dec.keywords:
                if kw.arg == "methods":
                    return ast.literal_eval(kw.value)
    return None


def _first_statement(fn):
    """The first real statement of the body — the docstring and the lazy
    `import frappe` some modules open with (they must import on the host, with
    no bench) are not statements a caller can be refused by."""
    body = fn.body
    if (body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)):
        body = body[1:]
    while body and isinstance(body[0], (ast.Import, ast.ImportFrom)):
        body = body[1:]
    assert body, f"{fn.name} has no body"
    return body[0]


def _only_for_roles(stmt):
    """The roles named by a `frappe.only_for(...)` statement, else None."""
    if not isinstance(stmt, ast.Expr) or not isinstance(stmt.value, ast.Call):
        return None
    call = stmt.value
    if not (isinstance(call.func, ast.Attribute) and call.func.attr == "only_for"
            and isinstance(call.func.value, ast.Name) and call.func.value.id == "frappe"):
        return None
    (arg,) = call.args
    roles = ast.literal_eval(arg)
    return {roles} if isinstance(roles, str) else set(roles)


def test_trigger_close_run_is_post_only():
    """It inserts a run, commits and enqueues the suite, so a GET must never
    reach it."""
    methods = _whitelist_methods(_function(ASSERTION_RUN, "trigger_close_run"))
    assert methods == ["POST"], f'trigger_close_run is not @frappe.whitelist(methods=["POST"]): {methods}'


def test_only_the_close_roles_may_start_a_close_run():
    """The gate is the first statement, ahead of the "already in progress"
    check, so a user without the role is refused whatever the run state."""
    roles = _only_for_roles(_first_statement(_function(ASSERTION_RUN, "trigger_close_run")))
    assert roles == {"EPM Admin", "System Manager"}, (
        f"trigger_close_run does not open with frappe.only_for of the close roles: {roles}")


def test_only_the_launch_roles_may_read_the_launch_options():
    """`launch_options` reads every Pipeline and the declared calendar with
    `frappe.get_all`, which ignores permissions, so the gate is the function's
    own first statement."""
    roles = _only_for_roles(_first_statement(_function(ORCHESTRATOR_API, "launch_options")))
    assert roles == {"EPM Admin", "EPM Analyst", "System Manager"}, (
        f"launch_options does not open with frappe.only_for of the launch roles: {roles}")

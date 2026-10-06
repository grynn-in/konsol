"""konsol#338 (#338-1, Deepak Pai 6 Oct 2026): every path that completes a dbt
build checks the signed periods' fingerprints afterwards
(``konsol.close.fingerprint.check_after_build``), so a signature is voided
where, and only where, the signed numbers changed.

The paths, enumerated from every place a build is marked Completed:

- ``tasks._finish_governed_build`` — a governed build (Build Approval).
- ``orchestrator.run.run_pipeline`` — an orchestrator run.
- ``tasks.run_pipeline`` — Pipeline Run's own trigger (Airbyte + dbt build).
- ``tasks._run_dbt_build_background`` — the deprecated dbt build that
  schema_apply still queues.

Each check runs only on success, after the build's own status is committed
(the check commits or rolls back on its own), and is a statement of the
function, not hidden in an ``except``. The check itself is tested in
test_close_fingerprint.py; the live run is in the PR.
"""
import ast
import os

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TASKS = os.path.join(APP_DIR, "tasks.py")
ORCH_RUN = os.path.join(APP_DIR, "orchestrator", "run.py")

PATHS = (
    (TASKS, "_finish_governed_build"),
    (ORCH_RUN, "run_pipeline"),
    (TASKS, "run_pipeline"),
    (TASKS, "_run_dbt_build_background"),
)


def _fn(path, name):
    with open(path) as f:
        src = f.read()
    tree = ast.parse(src)
    return src, next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name)


def _check_calls(fn):
    return [n for n in ast.walk(fn) if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute) and n.func.attr == "check_after_build"
            and getattr(n.func.value, "id", "") == "fingerprint"]


def _guard(fn, call):
    """The ``if`` test text enclosing ``call``, innermost first."""
    tests = []

    def walk(node, stack):
        if node is call:
            tests.extend(reversed(stack))
            return True
        for child in ast.iter_child_nodes(node):
            pushed = stack + [ast.unparse(node.test)] if isinstance(node, ast.If) and (
                child in node.body) else stack
            if walk(child, pushed):
                return True
        return False

    walk(fn, [])
    return tests


def test_every_build_completion_path_checks_the_signed_fingerprints_once():
    for path, name in PATHS:
        _, fn = _fn(path, name)
        calls = _check_calls(fn)
        assert len(calls) == 1, (name, len(calls))


def test_the_check_runs_only_on_a_successful_build():
    for path, name in PATHS:
        _, fn = _fn(path, name)
        (call,) = _check_calls(fn)
        guards = _guard(fn, call)
        assert guards and ("'Completed'" in guards[0] or "returncode == 0" in guards[0]), (
            name, guards)


def test_the_check_is_not_inside_an_except():
    for path, name in PATHS:
        _, fn = _fn(path, name)
        (call,) = _check_calls(fn)
        for handler in [n for n in ast.walk(fn) if isinstance(n, ast.ExceptHandler)]:
            assert call not in list(ast.walk(handler)), name


def test_the_check_follows_the_builds_own_commit():
    for path, name in PATHS:
        src, fn = _fn(path, name)
        body = ast.get_source_segment(src, fn)
        call = body.index("fingerprint.check_after_build(")
        commits = [i for i in range(len(body)) if body.startswith("frappe.db.commit()", i)]
        stamps = [i for i in range(len(body)) if body.startswith("_stamp_terminal_status(", i)
                  or body.startswith("_update_pipeline_run(", i)]
        assert any(i < call for i in commits + stamps), name


def test_the_governed_build_names_its_pipeline_run_and_approval():
    src, fn = _fn(TASKS, "_finish_governed_build")
    (call,) = _check_calls(fn)
    assert [ast.unparse(a) for a in call.args] == ["pipeline_run", "doc.name"]
    body = ast.get_source_segment(src, fn)
    # The follow-up request comes after the check, never before it.
    assert body.index("fingerprint.check_after_build(") < body.index("request_build_for_scope(")
    _, run = _fn(TASKS, "run_governed_build")
    finish = [n for n in ast.walk(run) if isinstance(n, ast.Call)
              and getattr(n.func, "id", "") == "_finish_governed_build"]
    assert [ast.unparse(a) for a in finish[0].args] == ["doc", "pipeline_run"]


def test_the_orchestrator_names_its_run_and_any_approval():
    _, fn = _fn(ORCH_RUN, "run_pipeline")
    (call,) = _check_calls(fn)
    assert ast.unparse(call.args[0]) == "run_name"

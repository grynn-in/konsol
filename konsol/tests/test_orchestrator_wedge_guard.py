"""Review #66 fixes: the single-flight guard must not wedge on a crashed run, and
the legacy trigger_pipeline path must honor the same guard. Site-free source/AST
assertions (the frappe-bound behavior needs a live bench).
"""
import ast
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _func_src(path, name):
    src = open(path).read()
    for n in ast.walk(ast.parse(src)):
        if isinstance(n, ast.FunctionDef) and n.name == name:
            return ast.get_source_segment(src, n)
    return None


def test_run_pipeline_stamps_terminal_status_on_crash():
    """An uncaught executor error must mark the run Failed (not leave it Running),
    else the single-flight guard would block all future runs."""
    body = _func_src(os.path.join(ROOT, "orchestrator", "run.py"), "run_pipeline")
    assert body is not None
    assert "except Exception" in body, "run_pipeline must catch executor crashes"
    assert "_stamp_terminal_status" in body, "crash path must stamp a terminal status"
    assert "raise" in body, "crash must still re-raise so RQ records the failure"


def test_trigger_pipeline_honors_single_flight_guard():
    body = _func_src(
        os.path.join(ROOT, "pipeline", "doctype", "pipeline_run", "pipeline_run.py"),
        "trigger_pipeline",
    )
    assert body is not None
    assert "_assert_no_active_run()" in body, "legacy trigger_pipeline must gate on the guard"


def test_trigger_pipeline_uses_single_flight_lock():
    # #67 fix 1: legacy path serialises with start_run via the shared DB lock.
    body = _func_src(
        os.path.join(ROOT, "pipeline", "doctype", "pipeline_run", "pipeline_run.py"),
        "trigger_pipeline",
    )
    assert "single_flight_lock()" in body, "trigger_pipeline must take the single-flight lock"


def test_run_governed_build_checks_guard_before_creating_run():
    # #67 fix 5: the governed build shares the single-flight guard, and the check
    # MUST come BEFORE it creates its own (active) Pipeline Run, or it self-blocks.
    body = _func_src(os.path.join(ROOT, "tasks.py"), "run_governed_build")
    assert body is not None
    assert "_assert_no_active_run()" in body, "run_governed_build must honor the guard"
    # match the actual call site (with its arg) so a comment mention can't fool us
    assert "_create_governed_pipeline_run(doc)" in body
    assert body.index("_assert_no_active_run()") < body.index("_create_governed_pipeline_run(doc)"), (
        "the single-flight check must run before the governed Pipeline Run is created"
    )

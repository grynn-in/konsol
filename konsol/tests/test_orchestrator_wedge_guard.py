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

"""B1 review fix (#60): state-mutating orchestrator endpoints must enforce the
EPM Admin role guard, consistent with the apply_schema/publish pattern. Site-free
AST assertion — a live-frappe permission test belongs in bench run-tests.
"""
import ast
import os

APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _func_src(path, name):
    src = open(path).read()
    for n in ast.walk(ast.parse(src)):
        if isinstance(n, ast.FunctionDef) and n.name == name:
            return ast.get_source_segment(src, n)
    return None


def test_orchestrator_mutating_endpoints_guarded():
    p = os.path.join(APP, "orchestrator", "api.py")
    for fn in ("start_run", "retry_step", "resume_run", "cancel_run"):
        body = _func_src(p, fn)
        assert body is not None, f"{fn} not found"
        assert "check_epm_admin()" in body, f"{fn} missing check_epm_admin() guard"


def test_control_start_process_guarded():
    body = _func_src(os.path.join(APP, "control_api.py"), "start_process")
    assert body is not None
    assert "check_epm_admin()" in body


def test_trigger_pipeline_guarded():
    # #67 fix 4: the legacy state-mutating trigger_pipeline must enforce the same
    # EPM Admin role guard as the orchestrator API.
    body = _func_src(
        os.path.join(APP, "pipeline", "doctype", "pipeline_run", "pipeline_run.py"),
        "trigger_pipeline",
    )
    assert body is not None
    assert "check_epm_admin()" in body, "trigger_pipeline missing check_epm_admin() guard"

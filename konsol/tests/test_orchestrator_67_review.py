"""Regression tests for the #69 review fixes on the #67 hardening:

- B1: single_flight_lock must refresh the read view (commit) after acquiring the
  lock, else REPEATABLE READ makes the guard an illusion.
- B2: run_governed_build must run its check+create INSIDE single_flight_lock.
- B3 / A1: the worker must honor ANY external terminal status (Cancelled AND a
  reaper-set Failed), not just Cancelled — at startup, mid-run persist, the
  between-steps cancel_check, and finalize.

Site-free source/AST assertions (the cross-worker behavior needs a live bench).
"""
import ast
import inspect
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read(path):
    return open(os.path.join(ROOT, path)).read()


def _func_src(path, name):
    src = _read(path)
    for n in ast.walk(ast.parse(src)):
        if isinstance(n, ast.FunctionDef) and n.name == name:
            return ast.get_source_segment(src, n)
    return None


def test_single_flight_lock_commits_after_acquire():
    body = _func_src("orchestrator/api.py", "single_flight_lock")
    assert body is not None
    # The commit (read-view refresh) must come AFTER GET_LOCK and BEFORE yield.
    get_lock = body.index("GET_LOCK")
    commit = body.index("frappe.db.commit()")
    yield_ = body.index("yield")
    assert get_lock < commit < yield_, "commit() must refresh the read view between GET_LOCK and yield"


def test_run_governed_build_check_and_create_inside_lock():
    body = _func_src("tasks.py", "run_governed_build")
    assert body is not None
    assert "with single_flight_lock():" in body, "check+create must be inside the named lock"
    # Within the with-block (slice past the marker, so comments above don't count):
    # the guard check must precede the create.
    after = body[body.index("with single_flight_lock():"):]
    assert after.index("_assert_no_active_run()") < after.index("_create_governed_pipeline_run(")


def test_run_pipeline_honors_any_external_terminal_status():
    src = _read("orchestrator/run.py")
    body = _func_src("orchestrator/run.py", "run_pipeline")
    assert body is not None
    # The terminal set must include BOTH Cancelled and Failed (reaper).
    assert "_EXTERNAL_TERMINAL" in body
    assert '"Cancelled"' in body and '"Failed"' in body
    # Startup, persist, cancel_check, and finalize must all gate on the set, not
    # on a bare == "Cancelled".
    assert body.count("in _EXTERNAL_TERMINAL") >= 3
    # And there must be no remaining bare-equality cancel check that would ignore
    # a reaper-set Failed.
    assert '== "Cancelled"' not in body, "use _EXTERNAL_TERMINAL membership, not == 'Cancelled'"

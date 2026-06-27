"""TDD — orchestrator whitelisted API (PRD-10).

The API module ``konsol.orchestrator.api`` exposes the ``@frappe.whitelist()``
entrypoints the SPA (PRD-11) and external callers use to drive runs:
``start_run`` / ``retry_step`` / ``resume_run`` / ``cancel_run``. Like the rest
of the orchestrator core it must import on the host **without** frappe (the
whitelist decorator degrades to a no-op when frappe is absent, and every frappe
call lives inside a function). The pure rebuild-state-from-rows helper lives in
``run.state_from_rows`` and is unit-tested here with plain fakes — no bench.
"""
import inspect

from konsol.orchestrator import api
from konsol.orchestrator import run
from konsol.orchestrator.dag import Dag, Step
from konsol.orchestrator.state import RunState, Status


class FakeRow:
    def __init__(self, **kw):
        self.__dict__.update(kw)


# ---- module import / surface --------------------------------------------

def test_api_module_imports_without_frappe():
    assert callable(api.start_run)
    assert callable(api.retry_step)
    assert callable(api.resume_run)
    assert callable(api.cancel_run)


def test_api_entrypoint_names_preserved():
    # the whitelist decorator must not mangle the public callables
    assert api.start_run.__name__ == "start_run"
    assert api.retry_step.__name__ == "retry_step"
    assert api.resume_run.__name__ == "resume_run"
    assert api.cancel_run.__name__ == "cancel_run"


def test_start_run_signature():
    sig = inspect.signature(api.start_run)
    assert "definition" in sig.parameters
    assert "params" in sig.parameters


def test_retry_step_signature():
    sig = inspect.signature(api.retry_step)
    assert "run_name" in sig.parameters
    assert "step_id" in sig.parameters


def test_resume_run_signature():
    sig = inspect.signature(api.resume_run)
    assert "run_name" in sig.parameters
    assert "step_id" in sig.parameters


def test_cancel_run_signature():
    sig = inspect.signature(api.cancel_run)
    assert "run_name" in sig.parameters


def test_run_pipeline_accepts_retry_and_resume_kwargs():
    sig = inspect.signature(run.run_pipeline)
    assert "retry_step" in sig.parameters
    assert "resume_from" in sig.parameters


# ---- state_from_rows (pure) ---------------------------------------------

def _dag():
    return Dag([
        Step("a", "t"),
        Step("b", "t", depends_on=["a"]),
        Step("c", "t", depends_on=["b"]),
    ])


def test_state_from_rows_maps_statuses():
    dag = _dag()
    rows = [
        FakeRow(step_id="a", status=Status.SUCCESS),
        FakeRow(step_id="b", status=Status.FAILED),
    ]
    state = run.state_from_rows(dag, rows)
    assert state.status("a") == Status.SUCCESS
    assert state.status("b") == Status.FAILED
    # a step with no persisted row stays Pending
    assert state.status("c") == Status.PENDING


def test_state_from_rows_ignores_unknown_step():
    dag = _dag()
    rows = [FakeRow(step_id="ghost", status=Status.SUCCESS)]
    state = run.state_from_rows(dag, rows)
    assert all(state.status(s.id) == Status.PENDING for s in dag.steps)


def test_state_from_rows_accepts_dict_rows():
    dag = _dag()
    rows = [{"step_id": "a", "status": Status.SUCCESS}]
    state = run.state_from_rows(dag, rows)
    assert state.status("a") == Status.SUCCESS


def test_state_from_rows_tolerates_none():
    dag = _dag()
    state = run.state_from_rows(dag, None)
    assert isinstance(state, RunState)
    assert all(state.status(s.id) == Status.PENDING for s in dag.steps)


def test_state_from_rows_then_retry_rearms_failed():
    dag = _dag()
    rows = [
        FakeRow(step_id="a", status=Status.SUCCESS),
        FakeRow(step_id="b", status=Status.FAILED),
        FakeRow(step_id="c", status=Status.PENDING),
    ]
    state = run.state_from_rows(dag, rows)
    state.retry("b")
    assert state.status("a") == Status.SUCCESS
    assert state.status("b") == Status.PENDING
    # the rebuilt state now offers the retried step as runnable
    assert [s.id for s in state.runnable()] == ["b"]


def test_state_from_rows_then_resume_resets_downstream():
    dag = _dag()
    rows = [
        FakeRow(step_id="a", status=Status.SUCCESS),
        FakeRow(step_id="b", status=Status.SUCCESS),
        FakeRow(step_id="c", status=Status.SUCCESS),
    ]
    state = run.state_from_rows(dag, rows)
    state.resume_from("b")
    assert state.status("a") == Status.SUCCESS
    assert state.status("b") == Status.PENDING
    assert state.status("c") == Status.PENDING

"""TDD — orchestrator executor (PRD-5). Pure-python (no frappe)."""
import pytest

from konsol.orchestrator.dag import Dag, Step
from konsol.orchestrator.executor import Executor
from konsol.orchestrator.handlers import StepResult
from konsol.orchestrator.state import RunState, Status


class FakeRegistry:
    """Duck-typed registry: ``.get(type)`` -> handler callable."""

    def __init__(self, handlers):
        self._handlers = handlers

    def get(self, step_type):
        return self._handlers[step_type]


class RecordingSink:
    """Records the executor's progress callbacks in call order."""

    def __init__(self):
        self.events = []

    def on_step_start(self, step):
        self.events.append(("start", step.id))

    def on_step_result(self, step, result):
        self.events.append(("result", step.id, result.ok))


def _ok_handler(ctx):
    return StepResult(ok=True, rows=1, log=f"ran {ctx.step.id}")


def _diamond_dag():
    # a -> b, a -> c, b/c -> d
    return Dag([
        Step("a", "t"),
        Step("b", "t", depends_on=["a"]),
        Step("c", "t", depends_on=["a"]),
        Step("d", "t", depends_on=["b", "c"]),
    ])


def test_happy_path_runs_all_to_success():
    dag = _diamond_dag()
    state = RunState(dag)
    sink = RecordingSink()
    reg = FakeRegistry({"t": _ok_handler})

    Executor(reg, sink).run(state)

    assert state.is_done()
    assert state.is_success()
    for sid in ("a", "b", "c", "d"):
        assert state.status(sid) == Status.SUCCESS
    # every step got a start and a result
    started = [e[1] for e in sink.events if e[0] == "start"]
    resulted = [e[1] for e in sink.events if e[0] == "result"]
    assert set(started) == {"a", "b", "c", "d"}
    assert set(resulted) == {"a", "b", "c", "d"}


def test_respects_dependency_order():
    dag = _diamond_dag()
    state = RunState(dag)
    sink = RecordingSink()
    Executor(FakeRegistry({"t": _ok_handler}), sink).run(state)

    started = [e[1] for e in sink.events if e[0] == "start"]
    # a before everything; d last
    assert started[0] == "a"
    assert started[-1] == "d"
    assert started.index("b") < started.index("d")
    assert started.index("c") < started.index("d")


def test_sink_callbacks_in_order():
    dag = Dag([Step("a", "t"), Step("b", "t", depends_on=["a"])])
    state = RunState(dag)
    sink = RecordingSink()
    Executor(FakeRegistry({"t": _ok_handler}), sink).run(state)

    assert sink.events == [
        ("start", "a"),
        ("result", "a", True),
        ("start", "b"),
        ("result", "b", True),
    ]


def test_failure_settles_run_and_blocks_descendants():
    # a -> b -> c ; b fails
    dag = Dag([
        Step("a", "t"),
        Step("b", "bad", depends_on=["a"]),
        Step("c", "t", depends_on=["b"]),
    ])
    state = RunState(dag)
    sink = RecordingSink()

    def _fail(ctx):
        return StepResult(ok=False, error="boom")

    reg = FakeRegistry({"t": _ok_handler, "bad": _fail})
    Executor(reg, sink).run(state)

    assert state.status("a") == Status.SUCCESS
    assert state.status("b") == Status.FAILED
    # downstream never ran
    assert state.status("c") == Status.PENDING
    assert state.has_failed()
    assert state.is_done()
    assert not state.is_success()
    # c was never started
    assert "c" not in [e[1] for e in sink.events if e[0] == "start"]


def test_handler_exception_marks_failed_not_crash():
    dag = Dag([Step("a", "boom")])
    state = RunState(dag)

    def _raise(ctx):
        raise RuntimeError("kaboom")

    reg = FakeRegistry({"boom": _raise})
    # must not propagate
    Executor(reg, None).run(state)

    assert state.status("a") == Status.FAILED
    assert state.is_done()
    assert state.has_failed()


def test_ctx_exposes_step_and_params():
    seen = {}

    def _capture(ctx):
        seen["step"] = ctx.step
        seen["params"] = ctx.params
        return StepResult(ok=True)

    step = Step("a", "t", params={"foo": "bar"})
    dag = Dag([step])
    state = RunState(dag)
    Executor(FakeRegistry({"t": _capture}), None).run(state)

    assert seen["step"] is step
    assert seen["params"] == {"foo": "bar"}


def test_cancel_stops_further_steps():
    # a -> b -> c chain; cancel during a
    dag = Dag([
        Step("a", "cancel"),
        Step("b", "t", depends_on=["a"]),
        Step("c", "t", depends_on=["b"]),
    ])
    state = RunState(dag)
    sink = RecordingSink()
    ex = Executor(None, sink)

    def _cancel(ctx):
        ex.cancel()
        return StepResult(ok=True)

    ex.registry = FakeRegistry({"cancel": _cancel, "t": _ok_handler})
    ex.run(state)

    assert state.status("a") == Status.SUCCESS
    # nothing downstream launched after cancel
    started = [e[1] for e in sink.events if e[0] == "start"]
    assert started == ["a"]
    assert state.status("b") != Status.SUCCESS
    assert state.status("c") != Status.SUCCESS


def test_cancel_check_stops_between_steps():
    # #67 fix 3a: a persisted (cross-worker) cancel is observed BETWEEN steps via
    # the injected cancel_check, halting the run cleanly before the next step.
    dag = Dag([
        Step("a", "t"),
        Step("b", "t", depends_on=["a"]),
        Step("c", "t", depends_on=["b"]),
    ])
    state = RunState(dag)
    sink = RecordingSink()

    flags = {"cancelled": False}

    def cancel_check():
        return flags["cancelled"]

    def _run_then_cancel(ctx):
        # after step "a" runs, simulate another worker persisting a cancel
        flags["cancelled"] = True
        return StepResult(ok=True)

    reg = FakeRegistry({"t": _ok_handler, "x": _run_then_cancel})
    # rebuild dag so step a uses the cancel-triggering handler
    dag = Dag([
        Step("a", "x"),
        Step("b", "t", depends_on=["a"]),
        Step("c", "t", depends_on=["b"]),
    ])
    state = RunState(dag)
    ex = Executor(reg, sink, cancel_check=cancel_check)
    ex.run(state)

    assert ex.cancelled is True
    assert state.status("a") == Status.SUCCESS
    # nothing downstream launched once the cancel was observed
    started = [e[1] for e in sink.events if e[0] == "start"]
    assert started == ["a"]
    assert state.status("b") == Status.PENDING
    assert state.status("c") == Status.PENDING


def test_cancel_check_none_is_noop():
    # default (no cancel_check) preserves the existing success path exactly
    dag = _diamond_dag()
    state = RunState(dag)
    Executor(FakeRegistry({"t": _ok_handler}), None).run(state)
    assert state.is_success()


def test_run_returns_state():
    dag = Dag([Step("a", "t")])
    state = RunState(dag)
    out = Executor(FakeRegistry({"t": _ok_handler}), None).run(state)
    assert out is state


def test_works_with_real_handler_registry_module():
    # the handlers module exposes .get and built-in stubs all return ok
    from konsol.orchestrator import handlers

    dag = Dag([Step("s", "signoff"), Step("x", "dbt_run", depends_on=["s"])])
    state = RunState(dag)
    Executor(handlers, None).run(state)
    assert state.is_success()

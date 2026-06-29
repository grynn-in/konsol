"""Executor — drives the run state machine to completion (PRD-5).

Pure-python (no frappe). Given a handler ``registry`` (anything with a
``.get(step_type) -> handler`` method, e.g. the :mod:`konsol.orchestrator.handlers`
module) and an optional progress ``sink``, the executor repeatedly picks the next
runnable step off a :class:`~konsol.orchestrator.state.RunState`, runs its handler
with a per-step context, records the result, and notifies the sink. On failure the
state machine already leaves descendants blocked, so the executor simply settles
the run. Cancellation stops launching any further steps.

The real Frappe sink + ``enqueue`` binding is PRD-9 — this module stays frappe-free.
"""
from __future__ import annotations

from typing import Optional

from konsol.orchestrator.handlers import StepResult
from konsol.orchestrator.state import RunState, Status


class StepContext:
    """Opaque execution context handed to a handler.

    Exposes the ``step`` being run and its ``params`` for convenience, plus an
    optional ``runner`` — a callable ``runner(argv) -> StepResult`` that the
    PRD-8 handlers delegate real execution to. On the pure host the runner is
    ``None`` (handlers only *build* their command); the PRD-9 Frappe binding
    injects a real runner via :class:`Executor`.
    """

    def __init__(self, step, runner=None):
        self.step = step
        self.params = step.params
        self.runner = runner


class Executor:
    """Drives a :class:`RunState` to a settled state.

    ``registry`` is any object exposing ``.get(step_type)`` returning a handler
    callable ``handler(ctx) -> StepResult``. ``sink`` is an optional observer; if
    provided, the executor calls ``sink.on_step_start(step)`` and
    ``sink.on_step_result(step, result)`` (both optional / duck-typed).
    """

    def __init__(self, registry, sink=None, runner=None, cancel_check=None):
        self.registry = registry
        self.sink = sink
        self.runner = runner
        # #67 fix 3a: optional ``cancel_check() -> bool`` polled BETWEEN steps so
        # a cross-worker cancel (which persists status="Cancelled" on the run doc)
        # stops the executor cleanly instead of crashing on the next persist. The
        # pure host leaves it None (no-op); the Frappe binding injects a closure
        # that reads the persisted Pipeline Run status.
        self.cancel_check = cancel_check
        self._cancelled = False

    def cancel(self) -> None:
        """Request cancellation; no further steps will be launched."""
        self._cancelled = True

    @property
    def cancelled(self) -> bool:
        return self._cancelled

    def _notify_start(self, step) -> None:
        if self.sink is not None and hasattr(self.sink, "on_step_start"):
            self.sink.on_step_start(step)

    def _notify_result(self, step, result: StepResult) -> None:
        if self.sink is not None and hasattr(self.sink, "on_step_result"):
            self.sink.on_step_result(step, result)

    def _run_step(self, step, state: RunState) -> None:
        state.mark(step.id, Status.RUNNING)
        self._notify_start(step)
        ctx = StepContext(step, runner=self.runner)
        try:
            handler = self.registry.get(step.type)
            result = handler(ctx)
        except Exception as exc:  # handler blew up — fail the step, don't crash
            result = StepResult(ok=False, error=str(exc))
        if not isinstance(result, StepResult):
            result = StepResult(ok=bool(result))
        state.mark(step.id, Status.SUCCESS if result.ok else Status.FAILED)
        self._notify_result(step, result)

    def run(self, state: RunState) -> RunState:
        """Run steps until the state settles (or cancellation). Returns ``state``."""
        while not state.is_done():
            if self._cancelled:
                break
            # #67 fix 3a: honor a persisted (cross-worker) cancel between steps.
            if self.cancel_check is not None and self.cancel_check():
                self._cancelled = True
                break
            runnable = state.runnable()
            if not runnable:
                break
            # deterministic: take the first runnable (declaration order)
            self._run_step(runnable[0], state)
        return state

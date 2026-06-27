"""Handler registry + real handlers (PRD-4, PRD-8).

Maps a step *type* (e.g. ``dbt_run``) to a callable that executes it. Pure-python
(no top-level frappe import) so the registry and the command builder unit-test on
host pytest.

A handler is any callable ``run(ctx) -> StepResult``. ``ctx`` is an opaque
execution context (the executor in PRD-5 supplies it). Handlers read ``params``
and an optional ``runner`` off ``ctx`` (tolerating a bare ``dict`` ctx too, for
unit tests). When no ``runner`` is attached (pure host / no runtime) the handler
*builds* and returns its command without executing it — the actual subprocess /
Airbyte / Frappe runner is injected by the PRD-9 Frappe binding.

The testable core is :func:`build_dbt_command`, a pure ``(verb, params) -> argv``
mapping that mirrors the param keys produced by :mod:`konsol.orchestrator.plan`
(``select`` from scope, ``full_refresh``, ``vars`` from fiscal year/period).
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Protocol, runtime_checkable


@dataclass
class StepResult:
    """Outcome of running a single step.

    ``ok`` is the only required field; ``rows`` reports affected/loaded rows,
    ``log`` carries human-readable output, ``error`` holds a failure message.
    """

    ok: bool
    rows: int = 0
    log: str = ""
    error: str = ""


@runtime_checkable
class Handler(Protocol):
    """Contract for a step handler: ``run(ctx) -> StepResult``."""

    def __call__(self, ctx) -> StepResult:  # pragma: no cover - structural
        ...


# type -> handler callable
_REGISTRY: Dict[str, Callable[..., StepResult]] = {}


def register(step_type: str) -> Callable[[Callable[..., StepResult]], Callable[..., StepResult]]:
    """Decorator registering a handler for ``step_type``.

    Raises :class:`ValueError` if ``step_type`` is already registered.
    """

    def _decorator(fn: Callable[..., StepResult]) -> Callable[..., StepResult]:
        if step_type in _REGISTRY:
            raise ValueError(f"handler already registered for type {step_type!r}")
        _REGISTRY[step_type] = fn
        return fn

    return _decorator


def get(step_type: str) -> Callable[..., StepResult]:
    """Return the handler for ``step_type``.

    Raises :class:`KeyError` if no handler is registered.
    """
    try:
        return _REGISTRY[step_type]
    except KeyError:
        raise KeyError(f"no handler registered for type {step_type!r}")


def registered_types() -> set:
    """All currently-registered step types."""
    return set(_REGISTRY)


# Built-in step types.
BUILTIN_TYPES = (
    "airbyte_sync",
    "dbt_seed",
    "dbt_run",
    "dbt_build",
    "dbt_test",
    "close_assertions",
    "signoff",
)

# dbt step type -> dbt CLI verb. Mirrors plan.DBT_TYPES.
DBT_VERB_BY_TYPE: Dict[str, str] = {
    "dbt_seed": "seed",
    "dbt_run": "run",
    "dbt_build": "build",
    "dbt_test": "test",
}


# ---- ctx accessors (tolerate a StepContext or a bare dict) ----

def _ctx_get(ctx, key, default=None):
    val = getattr(ctx, key, None)
    if val is None and isinstance(ctx, dict):
        val = ctx.get(key)
    return default if val is None else val


def _params(ctx) -> Dict:
    return _ctx_get(ctx, "params", {}) or {}


def _runner(ctx):
    """Optional callable ``runner(argv) -> StepResult`` injected by the runtime."""
    return _ctx_get(ctx, "runner", None)


# ---- pure command builder (the unit-testable core) ----

def build_dbt_command(verb: str, params: Optional[Dict]) -> List[str]:
    """Map a dbt ``verb`` + step ``params`` to a dbt CLI ``argv`` list.

    Honors the param keys produced by :mod:`konsol.orchestrator.plan`:
    ``select`` (from scope), ``full_refresh`` (incremental dbt steps), and
    ``vars`` (fiscal year/period), emitted as ``--vars '<json>'`` with sorted
    keys for deterministic output. Pure — no frappe, no subprocess.
    """
    params = params or {}
    argv: List[str] = ["dbt", verb]
    select = params.get("select")
    if select:
        argv += ["--select", select]
    if params.get("full_refresh"):
        argv += ["--full-refresh"]
    dbt_vars = params.get("vars")
    if dbt_vars:
        argv += ["--vars", json.dumps(dbt_vars, sort_keys=True)]
    return argv


# ---- handlers ----

def _make_dbt_handler(step_type: str, verb: str) -> Callable[..., StepResult]:
    def _handler(ctx) -> StepResult:
        argv = build_dbt_command(verb, _params(ctx))
        cmd = " ".join(argv)
        runner = _runner(ctx)
        if runner is None:
            # No runtime attached (pure host / planning) — return the command.
            return StepResult(ok=True, log=cmd)
        result = runner(argv)
        if isinstance(result, StepResult):
            return result
        return StepResult(ok=True, log=cmd)

    _handler.__name__ = f"handle_{step_type}"
    return _handler


def _handle_airbyte_sync(ctx) -> StepResult:
    """Trigger an Airbyte sync and record ``last_sync_at``.

    The real Airbyte client + ``last_sync_at`` write-back lives in the injected
    runner (PRD-9, needs frappe/HTTP). With no runner attached this is host-safe
    and returns a successful no-op.
    """
    runner = _runner(ctx)
    if runner is None:
        return StepResult(ok=True, log="airbyte_sync: no runtime — recorded on bind")
    result = runner(["airbyte_sync"])
    if isinstance(result, StepResult):
        return result
    return StepResult(ok=True, log="airbyte_sync")


def _handle_close_assertions(ctx) -> StepResult:
    """Run close-time assertions (frappe-bound at runtime via the runner)."""
    runner = _runner(ctx)
    if runner is None:
        return StepResult(ok=True, log="close_assertions: no runtime — no-op")
    result = runner(["close_assertions"])
    if isinstance(result, StepResult):
        return result
    return StepResult(ok=True, log="close_assertions")


def _handle_signoff(ctx) -> StepResult:
    """Mark the run signed off (frappe-bound at runtime via the runner)."""
    runner = _runner(ctx)
    if runner is None:
        return StepResult(ok=True, log="signoff: no runtime — no-op")
    result = runner(["signoff"])
    if isinstance(result, StepResult):
        return result
    return StepResult(ok=True, log="signoff")


register("airbyte_sync")(_handle_airbyte_sync)
for _t, _verb in DBT_VERB_BY_TYPE.items():
    register(_t)(_make_dbt_handler(_t, _verb))
register("close_assertions")(_handle_close_assertions)
register("signoff")(_handle_signoff)

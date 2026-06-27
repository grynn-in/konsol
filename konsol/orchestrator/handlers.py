"""Handler registry (PRD-4).

Maps a step *type* (e.g. ``dbt_run``) to a callable that executes it. Pure-python
(no top-level frappe import) so the registry unit-tests on host pytest. Real
handler bodies — command building, Airbyte calls, dbt invocation — land in PRD-8;
for now the built-in types register as stubs that return a successful
:class:`StepResult`.

A handler is any callable ``run(ctx) -> StepResult``. ``ctx`` is an opaque
execution context (the executor in PRD-5 supplies it); handlers must not assume
its concrete type beyond what they read.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Dict, Protocol, runtime_checkable


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


# Built-in step types — registered as stubs (real bodies land in PRD-8).
BUILTIN_TYPES = (
    "airbyte_sync",
    "dbt_seed",
    "dbt_run",
    "dbt_build",
    "dbt_test",
    "close_assertions",
    "signoff",
)


def _make_stub(step_type: str) -> Callable[..., StepResult]:
    def _stub(ctx) -> StepResult:
        return StepResult(ok=True, log=f"{step_type}: stub (PRD-4) — no-op")

    _stub.__name__ = f"stub_{step_type}"
    return _stub


for _t in BUILTIN_TYPES:
    register(_t)(_make_stub(_t))

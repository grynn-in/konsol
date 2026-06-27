"""Resume-from-step + retry planning core (PRD-15).

Pure-python (no frappe at top level). Given a definition's step list and the
persisted per-step statuses of a *settled* run, compute the reset status
snapshot for restarting the run from a chosen step (``plan_resume``) or for
re-arming a single failed step (``plan_retry``). Both reuse :class:`Dag` and
:class:`RunState` from the rest of the core.

A run is **settled** when nothing is ``Running`` and nothing is still runnable
(``RunState.is_done()``). Resuming/retrying a still-active run is rejected so we
never reset a step that is mid-flight or about to start.
"""
from __future__ import annotations

from typing import Dict, List

from konsol.orchestrator.dag import Dag, Step
from konsol.orchestrator.state import RunState


def _prepare(steps: List[Step], statuses: Dict[str, str], target: str) -> RunState:
    """Validate ``target`` exists and the run is settled; return a RunState."""
    dag = Dag(steps)
    if target not in {s.id for s in dag.steps}:
        raise ValueError(f"unknown step: {target!r}")
    state = RunState(dag, statuses)
    if not state.is_done():
        raise ValueError(
            "run is not settled (a step is Running or still runnable); "
            "cannot resume/retry"
        )
    return state


def plan_resume(steps: List[Step], statuses: Dict[str, str], from_step: str) -> Dict[str, str]:
    """Reset ``from_step`` and all its descendants to Pending; preserve upstream.

    Returns the new ``{step_id: status}`` snapshot. Raises ``ValueError`` on an
    unknown step or an unsettled run. Inputs are not mutated.
    """
    state = _prepare(steps, statuses, from_step)
    state.resume_from(from_step)
    return state.snapshot()


def plan_retry(steps: List[Step], statuses: Dict[str, str], step: str) -> Dict[str, str]:
    """Re-arm ``step`` (and its descendants) for another attempt.

    Same shape and validation as :func:`plan_resume`, via ``RunState.retry``.
    """
    state = _prepare(steps, statuses, step)
    state.retry(step)
    return state.snapshot()

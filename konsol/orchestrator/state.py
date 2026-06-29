"""Execution state machine (PRD-3).

Tracks per-step status over a :class:`Dag` and answers the questions the
executor needs: what is runnable now, are we done, what failed, and how to
reset for retry / resume-from-step. Pure-python (no frappe).
"""
from __future__ import annotations

from typing import Dict, List, Optional

from konsol.orchestrator.dag import Dag, Step


class Status:
    PENDING = "Pending"
    RUNNING = "Running"
    SUCCESS = "Success"
    FAILED = "Failed"
    SKIPPED = "Skipped"
    CANCELLED = "Cancelled"


# a dependency is satisfied if its provider reached one of these
_SATISFIED = {Status.SUCCESS, Status.SKIPPED}
# states a step can no longer leave on its own
_TERMINAL = {Status.SUCCESS, Status.FAILED, Status.SKIPPED, Status.CANCELLED}


class RunState:
    def __init__(self, dag: Dag, statuses: Optional[Dict[str, str]] = None):
        self.dag = dag
        self._status: Dict[str, str] = {s.id: Status.PENDING for s in dag.steps}
        if statuses:
            self._status.update(statuses)

    def status(self, step_id: str) -> str:
        return self._status[step_id]

    def mark(self, step_id: str, status: str) -> None:
        self._status[step_id] = status

    def _deps_satisfied(self, step: Step) -> bool:
        return all(self._status[d] in _SATISFIED for d in step.depends_on)

    def runnable(self) -> List[Step]:
        """Pending steps whose dependencies are all satisfied."""
        return [
            s for s in self.dag.steps
            if self._status[s.id] == Status.PENDING and self._deps_satisfied(s)
        ]

    def running(self) -> List[str]:
        return [sid for sid, st in self._status.items() if st == Status.RUNNING]

    def failed(self) -> set:
        return {sid for sid, st in self._status.items() if st == Status.FAILED}

    def has_failed(self) -> bool:
        return bool(self.failed())

    def is_done(self) -> bool:
        """Settled: nothing is running and nothing more can start."""
        return not self.running() and not self.runnable()

    def is_success(self) -> bool:
        return all(st in _SATISFIED for st in self._status.values())

    def _reset(self, step_id: str) -> None:
        """Reset a step and everything downstream of it to Pending."""
        self._status[step_id] = Status.PENDING
        for sid in self.dag.descendants(step_id):
            self._status[sid] = Status.PENDING

    def retry(self, step_id: str) -> None:
        """Re-arm a failed step (and its descendants) for another attempt."""
        self._reset(step_id)

    def resume_from(self, step_id: str) -> None:
        """Restart a finished run from ``step_id`` downward."""
        self._reset(step_id)

    def snapshot(self) -> Dict[str, str]:
        return dict(self._status)

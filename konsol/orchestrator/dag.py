"""Orchestrator DAG core (PRD-1).

Pure-python (no frappe import) so it unit-tests on host pytest. Represents a
pipeline run as a directed acyclic graph of typed steps and provides
topological ordering, cycle/validation checks, and dependency queries.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Set


class DagError(ValueError):
    """Raised for an invalid DAG (cycle, unknown/duplicate dependency)."""


@dataclass
class Step:
    """A single node in the pipeline DAG."""

    id: str
    type: str
    depends_on: List[str] = field(default_factory=list)
    params: Dict = field(default_factory=dict)


class Dag:
    """A validated directed acyclic graph of :class:`Step` nodes."""

    def __init__(self, steps: List[Step]):
        self._steps: Dict[str, Step] = {}
        for s in steps:
            if s.id in self._steps:
                raise DagError(f"duplicate step id: {s.id!r}")
            self._steps[s.id] = s
        for s in steps:
            for dep in s.depends_on:
                if dep not in self._steps:
                    raise DagError(f"unknown dependency {dep!r} for step {s.id!r}")
        self.steps = list(steps)

    def get(self, step_id: str) -> Step:
        return self._steps[step_id]

    def roots(self) -> List[Step]:
        """Steps with no dependencies, in declaration order."""
        return [s for s in self.steps if not s.depends_on]

    def dependents(self, step_id: str) -> Set[str]:
        """Direct dependents (steps that list ``step_id`` in depends_on)."""
        return {s.id for s in self.steps if step_id in s.depends_on}

    def descendants(self, step_id: str) -> Set[str]:
        """Transitive dependents of ``step_id`` (everything downstream)."""
        out: Set[str] = set()
        stack = list(self.dependents(step_id))
        while stack:
            cur = stack.pop()
            if cur in out:
                continue
            out.add(cur)
            stack.extend(self.dependents(cur))
        return out

    def toposort(self) -> List[Step]:
        """Return steps in a dependency-respecting order (Kahn's algorithm).

        Preserves declaration order among independent steps for stable output.
        Raises :class:`DagError` if the graph contains a cycle.
        """
        indeg = {sid: 0 for sid in self._steps}
        for s in self.steps:
            for _ in s.depends_on:
                indeg[s.id] += 1
        # ready queue keeps declaration order for determinism
        ready = [s for s in self.steps if indeg[s.id] == 0]
        order: List[Step] = []
        while ready:
            cur = ready.pop(0)
            order.append(cur)
            for dependent_id in sorted(
                self.dependents(cur.id),
                key=lambda sid: self.steps.index(self._steps[sid]),
            ):
                indeg[dependent_id] -= 1
                if indeg[dependent_id] == 0:
                    ready.append(self._steps[dependent_id])
        if len(order) != len(self.steps):
            remaining = sorted(set(self._steps) - {s.id for s in order})
            raise DagError(f"cycle detected among steps: {remaining}")
        return order

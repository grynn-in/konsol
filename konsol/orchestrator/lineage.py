"""Per-step metrics + lineage (PRD-16).

Pure-python (no frappe import) so it unit-tests on host pytest. Two concerns:

1. **Lineage** — a static :data:`STEP_OUTPUTS` map from step id to the tables it
   produces, plus :func:`lineage_for` which turns a plan into a flat edge list:
   the DAG dependency edges *and* the step->table edges. Useful for rendering a
   data-lineage view in the SPA.
2. **Metrics rollup** — :func:`summarize` consumes the ``{step_id: {rows,
   duration_s}}`` dict the PRD-9 ``FrappeSink`` records on each child row
   (``rows`` + ``started_at``/``ended_at``) and produces totals, a per-step
   breakdown, and a rolled-up run status. It is deliberately decoupled from the
   executor internals — it just consumes the snapshot + metrics dicts.
"""
from __future__ import annotations

from typing import Dict, List, Tuple

from konsol.orchestrator.dag import Dag, Step
from konsol.orchestrator.state import Status

# Static map: step id -> the table(s) (or glob) that step materialises.
# Mirrors the canonical Group Close plan (plan.DEFAULT_DEFINITION).
STEP_OUTPUTS: Dict[str, List[str]] = {
    "extract": ["epm_raw"],
    "seed": ["epm_seeds"],
    "silver": ["silver_*"],
    "gold": ["gold_*"],
    "assertions": ["close_assertions"],
    "signoff": [],
}

# states that count as "settled successfully" for the rollup
_OK = {Status.SUCCESS, Status.SKIPPED}


def lineage_for(plan: List[Step]) -> List[Tuple[str, str]]:
    """Return ``(upstream, downstream)`` edges for ``plan``.

    Two kinds of edge, in this order:
    - **dependency edges** between steps (``dep -> step``), and
    - **step->table edges** (``step -> produced_table``) from
      :data:`STEP_OUTPUTS`.
    """
    dag = Dag(plan)
    edges: List[Tuple[str, str]] = []
    # dependency edges (declaration order, then depends_on order)
    for step in dag.steps:
        for dep in step.depends_on:
            edges.append((dep, step.id))
    # step -> table edges
    for step in dag.steps:
        for table in STEP_OUTPUTS.get(step.id, []):
            edges.append((step.id, table))
    return edges


def _rollup_status(snapshot: Dict[str, str]) -> str:
    """Roll a per-step status snapshot up to a single run status."""
    values = list(snapshot.values())
    if not values:
        return Status.PENDING
    if any(v == Status.FAILED for v in values):
        return Status.FAILED
    if any(v == Status.CANCELLED for v in values):
        return Status.CANCELLED
    if any(v == Status.RUNNING for v in values):
        return Status.RUNNING
    if all(v in _OK for v in values):
        return Status.SUCCESS
    return Status.PENDING


def summarize(snapshot: Dict[str, str], metrics: Dict[str, Dict] = None) -> Dict:
    """Roll a status snapshot + per-step metrics into a run summary.

    ``snapshot`` is ``{step_id: status}``; ``metrics`` is
    ``{step_id: {"rows": int, "duration_s": number}}`` (as the Frappe sink
    records). Returns ``{total_rows, duration_s, per_step, status}`` where
    ``per_step`` is one ``{step_id, status, rows, duration_s}`` dict per step,
    in snapshot order. Missing metrics default to zero.
    """
    snapshot = snapshot or {}
    metrics = metrics or {}
    per_step: List[Dict] = []
    total_rows = 0
    total_duration = 0
    for step_id, status in snapshot.items():
        m = metrics.get(step_id) or {}
        rows = m.get("rows") or 0
        duration = m.get("duration_s") or 0
        total_rows += rows
        total_duration += duration
        per_step.append(
            {
                "step_id": step_id,
                "status": status,
                "rows": rows,
                "duration_s": duration,
            }
        )
    return {
        "total_rows": total_rows,
        "duration_s": total_duration,
        "per_step": per_step,
        "status": _rollup_status(snapshot),
    }

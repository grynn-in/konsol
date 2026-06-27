"""Whitelisted orchestrator API (PRD-10).

Thin Frappe layer over :mod:`konsol.orchestrator.run` (the executor binding) and
the pure state machine. These are the ``@frappe.whitelist()`` entrypoints the
konsol-exec SPA (PRD-11) and external callers use to drive runs:

- :func:`start_run` — create + stamp a Pipeline Run, enqueue it on a worker;
- :func:`retry_step` — re-arm a failed step (+ its descendants) and re-enqueue;
- :func:`resume_run` — restart a finished run from a chosen step downward;
- :func:`cancel_run` — request cancellation of a run.

Heavy work always runs in a worker via ``frappe.enqueue`` so the HTTP call
returns the run name immediately. Like the rest of the orchestrator core this
module imports on the host **without** frappe: the ``whitelist`` decorator
degrades to a no-op when frappe is absent, and every frappe call lives inside a
function. The pure rebuild-state-from-rows logic is :func:`run.state_from_rows`.
"""
from __future__ import annotations

from typing import Dict, Optional

try:  # frappe only exists inside a bench; host pytest must still import this module
    import frappe

    whitelist = frappe.whitelist
except Exception:  # pragma: no cover - host import path (no bench)

    def whitelist(*dargs, **dkwargs):
        def deco(fn):
            return fn

        return deco


# the qname of the enqueue-able worker entrypoint in run.py
_RUN_PIPELINE = "konsol.orchestrator.run.run_pipeline"


def _coerce_params(params) -> Dict:
    """Normalise the ``params`` arg (JSON string from HTTP, dict, or None)."""
    import frappe

    if params is None:
        return {}
    if isinstance(params, str):
        return frappe.parse_json(params) or {}
    return dict(params)


@whitelist()
def start_run(definition: Optional[str] = None, params=None) -> str:
    """Create a Pipeline Run, stamp the PRD-7 params, and enqueue it.

    ``params`` may be a dict or a JSON string with any of ``fiscal_year``,
    ``fiscal_period``, ``scope``, ``full_refresh``, ``skip_sync``. Returns the
    new run name immediately; the run executes on a background worker.
    """
    import frappe

    p = _coerce_params(params)
    doc = frappe.get_doc(
        {
            "doctype": "Pipeline Run",
            "status": "Queued",
            "triggered_by": frappe.session.user,
            "started_at": frappe.utils.now_datetime(),
            "pipeline_definition": definition,
            "fiscal_year": p.get("fiscal_year"),
            "fiscal_period": p.get("fiscal_period"),
            "scope": p.get("scope"),
            "full_refresh": 1 if p.get("full_refresh") else 0,
            "skip_sync": 1 if p.get("skip_sync") else 0,
        }
    )
    doc.insert(ignore_permissions=True)
    frappe.db.commit()

    frappe.enqueue(_RUN_PIPELINE, queue="default", timeout=1800, run_name=doc.name)
    return doc.name


@whitelist()
def retry_step(run_name: str, step_id: str) -> str:
    """Re-arm a failed ``step_id`` (and its descendants) and re-enqueue the run.

    The worker rebuilds the run state from the persisted child rows
    (:func:`run.state_from_rows`), calls ``RunState.retry(step_id)``, and only
    re-runs the failed step plus everything downstream of it.
    """
    import frappe

    run_doc = frappe.get_doc("Pipeline Run", run_name)
    run_doc.status = "Queued"
    run_doc.save(ignore_permissions=True)
    frappe.db.commit()

    frappe.enqueue(
        _RUN_PIPELINE, queue="default", timeout=1800, run_name=run_name, retry_step=step_id
    )
    return run_name


@whitelist()
def resume_run(run_name: str, step_id: str) -> str:
    """Restart a finished run from ``step_id`` downward and re-enqueue it.

    The worker rebuilds state from the persisted rows and calls
    ``RunState.resume_from(step_id)`` so the chosen step and all its descendants
    re-execute while upstream successes are preserved.
    """
    import frappe

    run_doc = frappe.get_doc("Pipeline Run", run_name)
    run_doc.status = "Queued"
    run_doc.save(ignore_permissions=True)
    frappe.db.commit()

    frappe.enqueue(
        _RUN_PIPELINE, queue="default", timeout=1800, run_name=run_name, resume_from=step_id
    )
    return run_name


@whitelist()
def cancel_run(run_name: str) -> str:
    """Request cancellation of a run.

    First cut: persist a ``Cancelled`` status. The in-process
    :class:`~konsol.orchestrator.executor.Executor` only observes
    ``cancel()`` while it is live in the worker, so a cross-worker cancel of an
    already-enqueued run is best-effort — the persisted status stops a not-yet-
    started run from being treated as active and is the signal the loop/runner
    bails on. A robust mid-flight cooperative cancel is a follow-up (P2).
    """
    import frappe

    run_doc = frappe.get_doc("Pipeline Run", run_name)
    run_doc.status = "Cancelled"
    run_doc.save(ignore_permissions=True)
    frappe.db.commit()
    return run_name

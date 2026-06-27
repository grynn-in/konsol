"""Frappe executor binding (PRD-9).

Ties the pure orchestrator core (:mod:`plan`, :mod:`dag`, :mod:`state`,
:mod:`executor`, :mod:`handlers`) to a **Pipeline Run** doc so it can run as an
enqueued background job:

1. load the Pipeline Run doc + read its PRD-7 run params,
2. :func:`plan.build_plan` → :class:`Dag` → :class:`RunState`,
3. drive :class:`Executor` with a :class:`FrappeSink` (updates the run's PRD-6
   child rows + ``frappe.publish_realtime`` for live UI) and a per-run *runner*
   that actually shells out to the dbt CLI / triggers Airbyte.

This module imports on the host **without frappe** — every frappe / subprocess
side effect lives *inside* a function. The pure pieces (param mapping, plan/
state construction, the sink) are unit-tested on host pytest; the frappe-bound
``run_pipeline`` / ``make_runner`` get a container smoke test.
"""
from __future__ import annotations

from datetime import datetime
from typing import Dict, Optional, Tuple

from konsol.orchestrator.dag import Dag
from konsol.orchestrator.executor import Executor
from konsol.orchestrator.handlers import StepResult
from konsol.orchestrator.plan import DEFAULT_DEFINITION, build_plan
from konsol.orchestrator.state import RunState, Status
from konsol.orchestrator import handlers


# ---- tolerant doc/row accessors (work on a Frappe doc or a bare dict) ----

def _doc_get(doc, key, default=None):
    if isinstance(doc, dict):
        return doc.get(key, default)
    return getattr(doc, key, default)


def _row_get(row, key, default=None):
    if isinstance(row, dict):
        return row.get(key, default)
    return getattr(row, key, default)


def _row_set(row, key, value) -> None:
    if isinstance(row, dict):
        row[key] = value
    else:
        setattr(row, key, value)


def _int_or_none(value):
    """Frappe Int fields default to 0 — treat 0/empty/None as "not set"."""
    if value in (None, 0, "", "0"):
        return None
    return int(value)


# ---- param mapping (pure) -----------------------------------------------

def params_from_doc(doc) -> Dict:
    """Map the Pipeline Run PRD-7 fields to :func:`build_plan` param keys.

    Reads ``fiscal_year``, ``fiscal_period``, ``scope``, ``full_refresh``,
    ``skip_sync`` and ``pipeline_definition`` off a Pipeline Run doc (or a bare
    dict). Frappe Checks become ``bool``; zero/empty Ints/strings become ``None``
    so ``build_plan`` omits the corresponding dbt ``vars`` / ``select``.
    """
    return {
        "fiscal_year": _int_or_none(_doc_get(doc, "fiscal_year")),
        "fiscal_period": _int_or_none(_doc_get(doc, "fiscal_period")),
        "scope": _doc_get(doc, "scope") or None,
        "full_refresh": bool(_doc_get(doc, "full_refresh")),
        "skip_sync": bool(_doc_get(doc, "skip_sync")),
        "pipeline_definition": _doc_get(doc, "pipeline_definition") or None,
    }


# ---- plan/state construction (pure) -------------------------------------

def plan_run(params: Optional[Dict]) -> Tuple[Dag, RunState]:
    """Resolve params into a concrete (:class:`Dag`, :class:`RunState`) pair.

    Uses :data:`plan.DEFAULT_DEFINITION` (P2 will make this definition-driven).
    """
    steps = build_plan(DEFAULT_DEFINITION, params or {})
    dag = Dag(steps)
    return dag, RunState(dag)


# ---- progress sink (pure, with a fake/real doc) -------------------------

def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


class FrappeSink:
    """Executor observer that mirrors progress onto a Pipeline Run doc.

    Implements the duck-typed ``on_step_start(step)`` / ``on_step_result(step,
    result)`` hooks the :class:`Executor` calls. Each step upserts a child row
    in the run's ``steps`` table (PRD-6 fields: ``step_id``, ``step_type``,
    ``status``, ``started_at``, ``ended_at``, ``rows``, ``output``, ``error``,
    ``retry_count``) and, if a ``publish`` callback is supplied, emits a live
    event. Pure — the doc/row may be a real Frappe doc or a plain fake; the
    frappe ``publish_realtime`` wiring is injected by :func:`run_pipeline`.
    """

    def __init__(self, run_doc, child_field: str = "steps", publish=None, now=None):
        self.run_doc = run_doc
        self.child_field = child_field
        self.publish = publish
        self._now = now or _now
        self._rows: Dict[str, object] = {}

    def _rows_list(self):
        existing = _doc_get(self.run_doc, self.child_field, None)
        if existing is None:
            _row_set(self.run_doc, self.child_field, [])
            existing = _doc_get(self.run_doc, self.child_field)
        return existing

    def _append_row(self, values: Dict):
        doc = self.run_doc
        if hasattr(doc, "append"):
            return doc.append(self.child_field, values)
        row = dict(values)
        self._rows_list().append(row)
        return row

    def _get_or_create(self, step):
        cached = self._rows.get(step.id)
        if cached is not None:
            return cached
        for r in self._rows_list():
            if _row_get(r, "step_id") == step.id:
                self._rows[step.id] = r
                return r
        row = self._append_row({"step_id": step.id, "step_type": step.type})
        self._rows[step.id] = row
        return row

    def _emit(self, event: str, step, status: str) -> None:
        if self.publish is None:
            return
        self.publish(event, {"step_id": step.id, "step_type": step.type, "status": status})

    def on_step_start(self, step) -> None:
        row = self._get_or_create(step)
        _row_set(row, "step_type", step.type)
        _row_set(row, "status", Status.RUNNING)
        _row_set(row, "started_at", self._now())
        _row_set(row, "error", "")
        self._emit("orchestrator_step", step, Status.RUNNING)

    def on_step_result(self, step, result: StepResult) -> None:
        row = self._get_or_create(step)
        status = Status.SUCCESS if result.ok else Status.FAILED
        _row_set(row, "step_type", step.type)
        _row_set(row, "status", status)
        _row_set(row, "ended_at", self._now())
        _row_set(row, "rows", getattr(result, "rows", 0) or 0)
        _row_set(row, "output", getattr(result, "log", "") or "")
        _row_set(row, "error", getattr(result, "error", "") or "")
        self._emit("orchestrator_step", step, status)


# ---- per-run runner (frappe / subprocess bound) -------------------------

def make_runner(run_doc, params: Optional[Dict] = None):
    """Build the runtime ``runner(argv) -> StepResult`` injected into the executor.

    Dispatches on ``argv[0]``:
    - ``"dbt"`` → shell out to the dbt CLI in the dbt-project dir;
    - ``"airbyte_sync"`` → trigger an Airbyte sync and write back ``last_sync_at``;
    - ``"close_assertions"`` / ``"signoff"`` → frappe-side close steps.

    All frappe / subprocess imports stay inside this closure so the module loads
    on the host without frappe.
    """
    params = params or {}

    def runner(argv):
        kind = argv[0] if argv else ""
        if kind == "dbt":
            return _run_dbt(argv)
        if kind == "airbyte_sync":
            return _run_airbyte_sync(run_doc)
        if kind == "close_assertions":
            return _run_close_assertions(run_doc, params)
        if kind == "signoff":
            return _run_signoff(run_doc)
        return StepResult(ok=True, log=" ".join(argv))

    return runner


def _run_dbt(argv) -> StepResult:
    import subprocess

    import frappe

    cwd = frappe.conf.get("dbt_project_dir") or frappe.get_site_path("..", "dbt_project")
    proc = subprocess.run(argv, cwd=cwd, capture_output=True, text=True)
    ok = proc.returncode == 0
    log = (proc.stdout or "") + (proc.stderr or "")
    return StepResult(ok=ok, log=log.strip(), error="" if ok else log.strip()[-2000:])


def _run_airbyte_sync(run_doc) -> StepResult:
    import frappe

    from konsol.services.airbyte_service import run_airbyte_sync

    rows = run_airbyte_sync()
    frappe.db.set_single_value("EPM Settings", "last_sync_at", frappe.utils.now())
    return StepResult(ok=True, rows=int(rows or 0), log="airbyte sync complete")


def _run_close_assertions(run_doc, params) -> StepResult:
    from konsol.close.close_assertions import run_close_assertions

    result = run_close_assertions(params)
    ok = bool(getattr(result, "ok", True))
    return StepResult(ok=ok, log=str(result))


def _run_signoff(run_doc) -> StepResult:
    return StepResult(ok=True, log="signed off")


# ---- enqueue-able entrypoint --------------------------------------------

def run_pipeline(run_name: str) -> RunState:
    """Load a Pipeline Run, plan it, and drive it to completion.

    Intended to be enqueued (``frappe.enqueue``). Updates the run's child rows +
    overall status as it goes and publishes live events for the UI.
    """
    import frappe

    run_doc = frappe.get_doc("Pipeline Run", run_name)
    params = params_from_doc(run_doc)
    dag, state = plan_run(params)

    def publish(event, payload):
        frappe.publish_realtime(
            event, payload, doctype="Pipeline Run", docname=run_name
        )

    sink = FrappeSink(run_doc, publish=publish)
    runner = make_runner(run_doc, params)

    if hasattr(run_doc, "status"):
        run_doc.status = Status.RUNNING
        run_doc.save(ignore_permissions=True)
        frappe.db.commit()

    Executor(handlers, sink=sink, runner=runner).run(state)

    if hasattr(run_doc, "status"):
        run_doc.status = Status.SUCCESS if state.is_success() else Status.FAILED
    run_doc.save(ignore_permissions=True)
    frappe.db.commit()
    return state

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

def plan_run(params: Optional[Dict], definition=None) -> Tuple[Dag, RunState]:
    """Resolve params into a concrete (:class:`Dag`, :class:`RunState`) pair.

    ``definition`` is the list of :class:`Step` templates to plan from. When
    ``None`` (the default, and every run with no ``pipeline_definition``) we fall
    back to :data:`plan.DEFAULT_DEFINITION` — so the behaviour is unchanged for
    existing runs. The frappe-bound :func:`run_pipeline` loads a user-authored
    Pipeline Definition (via :func:`definition.load_definition`) and passes the
    resulting steps here when the run carries a ``pipeline_definition``.
    """
    steps = build_plan(DEFAULT_DEFINITION if definition is None else definition, params or {})
    dag = Dag(steps)
    return dag, RunState(dag)


def progress_pct(state: RunState) -> int:
    """Percentage of steps that reached a satisfied terminal (Success/Skipped).

    Pure helper used to stamp ``Pipeline Run.progress_pct`` at finalize: 100 on a
    fully-successful run, a partial value when some steps failed. Empty plan -> 0.
    """
    snap = state.snapshot()
    if not snap:
        return 0
    done = sum(1 for st in snap.values() if st in (Status.SUCCESS, Status.SKIPPED))
    return int(round(100 * done / len(snap)))


def rows_synced_from_doc(run_doc) -> int:
    """Sum the rows reported by extract (``airbyte_sync``) steps of a run doc.

    Pure: reads the persisted child rows. Used to stamp ``rows_synced`` on the
    Pipeline Run so the History card shows the real extract volume. Returns 0
    when the run skipped sync (no extract steps).
    """
    total = 0
    for r in _doc_get(run_doc, "steps", []) or []:
        if _row_get(r, "step_type") == "airbyte_sync":
            total += int(_row_get(r, "rows", 0) or 0)
    return total


def state_from_rows(dag: Dag, rows) -> RunState:
    """Rebuild a :class:`RunState` from a run's persisted PRD-6 child rows.

    Used by retry / resume (PRD-10): map each saved step row's ``status`` back
    onto the freshly-planned :class:`Dag`. Rows may be Frappe child docs or bare
    dicts; rows whose ``step_id`` is not in the plan (e.g. a definition change)
    are ignored, and steps with no row stay :data:`Status.PENDING`. Pure.
    """
    known = {s.id for s in dag.steps}
    statuses: Dict[str, str] = {}
    for r in rows or []:
        sid = _row_get(r, "step_id")
        status = _row_get(r, "status")
        if sid in known and status:
            statuses[sid] = status
    return RunState(dag, statuses=statuses)


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

    def __init__(self, run_doc, child_field: str = "steps", publish=None, now=None, persist=None):
        self.run_doc = run_doc
        self.child_field = child_field
        self.publish = publish
        self.persist = persist
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
        self._persist()
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
        self._persist()
        self._emit("orchestrator_step", step, status)

    def _persist(self) -> None:
        """Flush the run doc so the UI can read step rows mid-run (frappe binding
        injects this; pure host tests leave it None -> no-op)."""
        if self.persist is not None:
            self.persist()


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
    import os
    import subprocess

    import frappe

    # Resolve the dbt project dir the same way the legacy build does — from
    # EPM Settings.dbt_project_path (the container path) — not a guessed
    # site-relative path. Falls back to conf / site path for safety.
    project = (
        frappe.conf.get("dbt_project_dir")
        or frappe.db.get_single_value("EPM Settings", "dbt_project_path")
        or frappe.get_site_path("..", "dbt_project")
    )
    # build_dbt_command yields ["dbt", <verb>, <flags...>]; the runtime injects
    # the env-specifics the pure builder can't know: the venv dbt binary and the
    # explicit project/profiles dirs (profiles.yml lives in the project dir).
    bench = frappe.utils.get_bench_path()
    dbt_bin = os.path.join(bench, "env", "bin", "dbt")
    if not os.path.exists(dbt_bin):
        dbt_bin = "dbt"
    verb = list(argv[1:2])
    flags = list(argv[2:])
    # NOTE: ``--profiles-dir`` is pointed at the dbt *project* dir, which means a
    # ``profiles.yml`` MUST live alongside ``dbt_project.yml`` in that dir (the
    # deploy/configurator writes one there). dbt will not find a profile under
    # ``~/.dbt`` with this invocation — keep profiles.yml in the project dir.
    cmd = [dbt_bin] + verb + ["--project-dir", project, "--profiles-dir", project] + flags
    proc = subprocess.run(cmd, cwd=project, capture_output=True, text=True)
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
    """Run the close assertion suite as ``dbt test`` — the same singular tests
    the Close Run uses (``--select test_type:singular --store-failures``),
    scoped to this run's fiscal period / entity via dbt vars so the assertions
    check the slice that was just built. The step fails iff dbt reports a failing
    assertion (non-zero exit). Uses the orchestrator's own dbt runner (resolves
    the project dir + venv dbt), not a Close Run document."""
    import json

    argv = ["dbt", "test", "--select", "test_type:singular", "--store-failures"]
    dbt_vars = {}
    for key in ("fiscal_year", "fiscal_period"):
        val = (params or {}).get(key)
        if val is not None and str(val) != "":
            dbt_vars[key] = val
    scope = (params or {}).get("scope")
    if scope:
        dbt_vars["entity_scope"] = scope
    if dbt_vars:
        argv += ["--vars", json.dumps(dbt_vars, sort_keys=True)]
    return _run_dbt(argv)


def _run_signoff(run_doc) -> StepResult:
    return StepResult(ok=True, log="signed off")


# ---- enqueue-able entrypoint --------------------------------------------

def _stamp_terminal_status(run_name, run_doc, final_status, state) -> None:
    """Write the parent run's terminal status + #65b metadata.

    Uses ``frappe.db.set_value`` (not ``run_doc.save``) so a concurrent cancel's
    bump of ``modified`` can't raise a TimestampMismatch on this final write.
    """
    import frappe

    completed_at = frappe.utils.now_datetime()
    updates = {
        "status": final_status,
        "completed_at": completed_at,
        "progress_pct": progress_pct(state),
        "rows_synced": rows_synced_from_doc(run_doc),
    }
    started_at = _doc_get(run_doc, "started_at")
    if started_at:
        delta = completed_at - frappe.utils.get_datetime(started_at)
        updates["duration_seconds"] = max(int(delta.total_seconds()), 0)
    frappe.db.set_value("Pipeline Run", run_name, updates)
    frappe.db.commit()


def run_pipeline(run_name: str, retry_step=None, resume_from=None) -> RunState:
    """Load a Pipeline Run, plan it, and drive it to completion.

    Intended to be enqueued (``frappe.enqueue``). Updates the run's child rows +
    overall status as it goes and publishes live events for the UI.

    For a fresh run all steps start :data:`Status.PENDING`. When ``retry_step``
    or ``resume_from`` is given (PRD-10 retry / resume), the run state is
    rebuilt from the persisted child rows via :func:`state_from_rows` and then
    re-armed (``RunState.retry`` / ``RunState.resume_from``) so only the failed
    step / the chosen step and everything downstream re-execute.
    """
    import frappe

    run_doc = frappe.get_doc("Pipeline Run", run_name)
    params = params_from_doc(run_doc)
    # Universal Airbyte guard: the single global EPM Settings.skip_airbyte_sync
    # flag governs every path (this orchestrator + the legacy build in tasks.py).
    # When on, force-skip the extract step for every run — there is no per-run
    # skip toggle. Default off => extract runs normally.
    if frappe.db.get_single_value("EPM Settings", "skip_airbyte_sync"):
        params["skip_sync"] = True

    # PRD-13 wiring (#65a): plan from the run's user-authored Pipeline Definition
    # when one is set, else fall back to DEFAULT_DEFINITION. Backward-compatible:
    # runs with no ``pipeline_definition`` behave exactly as before.
    definition_steps = None
    defn_name = params.get("pipeline_definition")
    if defn_name:
        from konsol.orchestrator.definition import load_definition

        definition_steps = load_definition(defn_name)
    dag, state = plan_run(params, definition=definition_steps)

    if retry_step or resume_from:
        state = state_from_rows(dag, _doc_get(run_doc, "steps", []))
        if retry_step:
            state.retry(retry_step)
        if resume_from:
            state.resume_from(resume_from)

    def publish(event, payload):
        # #64c realtime-room fix: publish UNSCOPED. With no room/user/doctype args,
        # frappe.publish_realtime falls through to the site room (broadcast to all
        # Desk users), which the konsol-exec SPA's global
        # ``frappe.realtime.on("orchestrator_step")`` receives. A doc-scoped room
        # (doctype/docname) only reaches a client that has joined that doc room
        # (i.e. has the Frappe Form open) — the SPA never does, so scoped events
        # were silently dropped. ``run`` is added so a client can tell which run an
        # event belongs to. (Single-admin app, so a site-room broadcast is fine; a
        # future multi-user UI could scope this with ``user=``.)
        frappe.publish_realtime(event, {**payload, "run": run_name})

    def persist():
        # Flush child rows mid-run so the live timeline reflects progress.
        #
        # #67 fix 3b: a concurrent ``cancel_run`` persists status="Cancelled" and
        # bumps Pipeline Run.modified. A naive ``run_doc.save()`` would then (a)
        # raise TimestampMismatchError and crash the RQ job, and (b) clobber the
        # persisted "Cancelled" back to "Running". Guard both: honor a persisted
        # Cancelled in our in-memory doc, and adopt the DB ``modified`` so the
        # optimistic-lock check passes. The parent's terminal status is owned by
        # the finalize path (``_stamp_terminal_status`` via set_value), not here.
        latest = frappe.db.get_value(
            "Pipeline Run", run_name, ["status", "modified"], as_dict=True
        )
        if latest:
            if latest.get("status") == "Cancelled":
                run_doc.status = "Cancelled"
            if latest.get("modified"):
                run_doc._original_modified = latest.get("modified")
                run_doc.modified = latest.get("modified")
        run_doc.save(ignore_permissions=True)
        frappe.db.commit()

    def cancel_check():
        # #67 fix 3a: stop the executor cleanly between steps if another worker
        # has persisted a cancel for this run.
        return frappe.db.get_value("Pipeline Run", run_name, "status") == "Cancelled"

    sink = FrappeSink(run_doc, publish=publish, persist=persist)
    runner = make_runner(run_doc, params)

    if hasattr(run_doc, "status"):
        run_doc.status = Status.RUNNING
        run_doc.save(ignore_permissions=True)
        frappe.db.commit()

    try:
        Executor(handlers, sink=sink, runner=runner, cancel_check=cancel_check).run(state)
    except Exception:
        # #64 wedge guard: an uncaught executor error must NOT leave the run stuck
        # in "Running". With the single-flight guard in place, a wedged Running run
        # would block every future start_run until someone manually cancels it.
        # Stamp a terminal "Failed" (unless a concurrent cancel already won) and
        # re-raise so RQ still records the job failure.
        if frappe.db.get_value("Pipeline Run", run_name, "status") != "Cancelled":
            _stamp_terminal_status(run_name, run_doc, Status.FAILED, state)
        raise

    # Finalize. The per-step rows were already flushed by ``persist()`` during the
    # run, so the only writes left are the parent's terminal status + run metadata.
    #
    # #64b cancel/save race: a concurrent ``cancel_run`` may have persisted
    # "Cancelled" while we were executing. Re-read the DB status first and HONOR a
    # persisted Cancelled — don't clobber it back to Completed/Failed.
    persisted_status = frappe.db.get_value("Pipeline Run", run_name, "status")
    if persisted_status == "Cancelled":
        return state

    # Pipeline Run.status uses the legacy vocabulary where "Completed" is the
    # success terminal (there is no "Success" option on the parent doc).
    # #65b run metadata (completed_at / duration_seconds / progress_pct /
    # rows_synced) is stamped by the shared helper so the History card shows real
    # values instead of "—"/0.
    final_status = "Completed" if state.is_success() else Status.FAILED
    _stamp_terminal_status(run_name, run_doc, final_status, state)
    return state

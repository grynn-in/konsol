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

import contextlib
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

# Pipeline Run statuses that mean a run still "owns" the dbt project dir — a new
# run must not be launched while any of these exists (#64a single-flight). These
# are exactly the non-terminal Pipeline Run.status options; the terminal ones are
# Completed / Failed / Cancelled.
ACTIVE_RUN_STATES = ("Queued", "Extracting", "Transforming", "Running")

# MariaDB named (advisory) lock that serialises the single-flight critical
# section (#67 fix 1). The SELECT-then-INSERT in ``_assert_no_active_run`` +
# create-run was a TOCTOU: two concurrent ``start_run`` / ``trigger_pipeline``
# calls could both pass the check and both insert an active run. GET_LOCK makes
# the check+insert atomic across DB connections (workers / web). The lock is
# session-scoped (held across ``COMMIT``, auto-released if the connection dies),
# so a crashed caller can never wedge it permanently.
_SINGLE_FLIGHT_LOCK = "konsol_pipeline_single_flight"
# Seconds GET_LOCK waits for the lock before giving up. Short — the critical
# section is just a SELECT + INSERT, so any real contention clears in well under
# a second; a longer wait would only mask a stuck holder.
_SINGLE_FLIGHT_TIMEOUT = 10


@contextlib.contextmanager
def single_flight_lock(timeout: int = _SINGLE_FLIGHT_TIMEOUT):
    """Serialise the single-flight check+insert under a MariaDB named lock.

    Both :func:`start_run` and the legacy
    :func:`pipeline_run.trigger_pipeline` wrap their ``_assert_no_active_run()``
    + create-run in ``with single_flight_lock():`` so the two paths can't race
    each other into two simultaneously-active runs. GET_LOCK returns ``1`` on
    acquire, ``0`` on timeout, ``NULL`` on error — anything but ``1`` raises a
    clear frappe error. RELEASE_LOCK always runs in ``finally``.
    """
    import frappe

    got = frappe.db.sql("SELECT GET_LOCK(%s, %s)", (_SINGLE_FLIGHT_LOCK, timeout))
    acquired = bool(got and got[0] and got[0][0] == 1)
    if not acquired:
        frappe.throw(
            "Could not acquire the pipeline single-flight lock — another run is "
            "being started right now. Try again in a moment.",
            frappe.ValidationError,
        )
    # CRITICAL: refresh the transaction's read view now that we hold the lock.
    # MariaDB runs REPEATABLE READ with autocommit off, so InnoDB pins this
    # request's consistent snapshot at its FIRST read — which happens before we
    # acquire the lock (session/CSRF setup, check_epm_admin -> get_roles). Without
    # this commit, a caller that blocked on GET_LOCK would acquire it *after* a
    # competitor committed its run, yet still read the stale pre-competitor
    # snapshot in _assert_no_active_run() and miss the row -> two active runs.
    # commit() starts a fresh read view; a named lock is independent of the
    # transaction, so it survives the commit.
    frappe.db.commit()
    try:
        yield
    finally:
        frappe.db.sql("SELECT RELEASE_LOCK(%s)", (_SINGLE_FLIGHT_LOCK,))


def _assert_no_active_run() -> None:
    """Single-flight guard (#64a): refuse a new run while one is already active.

    Two concurrent runs would shell out ``dbt`` against the one shared project dir
    at once (corrupting ``target/`` and racing incremental models). ``start_run``
    (and, transitively, the scheduler tick that calls it) and the legacy
    ``pipeline_run.trigger_pipeline`` are gated here. Retry / resume do NOT pass
    through this guard — they re-enqueue an *existing* run.
    """
    import frappe

    existing = frappe.get_all(
        "Pipeline Run",
        filters={"status": ["in", list(ACTIVE_RUN_STATES)]},
        fields=["name"],
        limit=1,
    )
    if existing:
        frappe.throw(
            f"A pipeline run is already active ({existing[0]['name']}). Wait for it "
            "to finish or cancel it before starting another.",
            frappe.ValidationError,
        )


def _coerce_params(params) -> Dict:
    """Normalise the ``params`` arg (JSON string from HTTP, dict, or None)."""
    import frappe

    if params is None:
        return {}
    if isinstance(params, str):
        return frappe.parse_json(params) or {}
    return dict(params)


def _steps_and_statuses(run_doc):
    """Rebuild ``(steps, statuses)`` from a run's persisted PRD-6 child rows.

    Each child row carries ``step_id``/``step_type``/``depends_on`` (a JSON array
    of upstream ids) and ``status``. This is the seam the pure PRD-15 planners
    (:func:`resume.plan_resume` / :func:`resume.plan_retry`) validate + reset.
    """
    import frappe

    from konsol.orchestrator.dag import Step

    steps = []
    statuses: Dict[str, str] = {}
    for row in run_doc.steps or []:
        deps = frappe.parse_json(row.depends_on) if row.depends_on else []
        steps.append(Step(id=row.step_id, type=row.step_type, depends_on=list(deps or [])))
        statuses[row.step_id] = row.status
    return steps, statuses


def _persist_statuses(run_doc, snapshot: Dict[str, str]) -> None:
    """Write the reset snapshot back onto the run's child rows in place."""
    for row in run_doc.steps or []:
        if row.step_id in snapshot:
            row.status = snapshot[row.step_id]


@whitelist()
def start_run(definition: Optional[str] = None, params=None) -> str:
    """Create a Pipeline Run, stamp the PRD-7 params, and enqueue it.

    ``params`` may be a dict or a JSON string with any of ``fiscal_year``,
    ``fiscal_period``, ``scope``, ``full_refresh``, ``skip_sync``. Returns the
    new run name immediately; the run executes on a background worker.
    """
    import frappe

    from konsol.schema_lifecycle import check_epm_admin

    check_epm_admin()
    p = _coerce_params(params)
    # #67 fix 1: hold the single-flight lock across the check AND the insert so a
    # concurrent start can't slip a second active run between them.
    with single_flight_lock():
        _assert_no_active_run()
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
def get_run(run_name: str) -> Dict:
    """Return a Pipeline Run snapshot for the konsol-exec timeline.

    Shape: ``{name, status, steps:[{step_id, step_type, status, started_at,
    ended_at, rows, output, error}]}`` — the PRD-6 child rows the SPA
    normalises via ``runModel.normalizeRun``. Read-only; safe to poll or call
    off the ``orchestrator_step`` realtime event.
    """
    import frappe

    run_doc = frappe.get_doc("Pipeline Run", run_name)
    steps = []
    for row in run_doc.steps or []:
        steps.append(
            {
                "step_id": row.step_id,
                "step_type": row.step_type,
                "status": row.status,
                "started_at": row.started_at,
                "ended_at": row.ended_at,
                "rows": row.rows,
                "output": row.output,
                "error": row.error,
            }
        )
    return {"name": run_doc.name, "status": run_doc.status, "steps": steps}


@whitelist()
def retry_step(run_name: str, step_id: str) -> str:
    """Re-arm a failed ``step_id`` (and its descendants) and re-enqueue the run.

    The run is validated as *settled* and the reset is computed by the pure
    PRD-15 planner (:func:`resume.plan_retry`): the failed step plus everything
    downstream of it is reset to Pending, upstream successes preserved. The
    worker then re-runs only that subtree (:func:`run.state_from_rows`).
    """
    import frappe

    from konsol.orchestrator import resume
    from konsol.schema_lifecycle import check_epm_admin

    check_epm_admin()
    run_doc = frappe.get_doc("Pipeline Run", run_name)
    steps, statuses = _steps_and_statuses(run_doc)
    snapshot = resume.plan_retry(steps, statuses, step_id)
    _persist_statuses(run_doc, snapshot)
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

    The run is validated as *settled* and the reset is computed by the pure
    PRD-15 planner (:func:`resume.plan_resume`): the chosen step and all its
    descendants are reset to Pending while upstream successes are preserved. The
    worker then re-executes that subtree (:func:`run.state_from_rows`).
    """
    import frappe

    from konsol.orchestrator import resume
    from konsol.schema_lifecycle import check_epm_admin

    check_epm_admin()

    run_doc = frappe.get_doc("Pipeline Run", run_name)
    steps, statuses = _steps_and_statuses(run_doc)
    snapshot = resume.plan_resume(steps, statuses, step_id)
    _persist_statuses(run_doc, snapshot)
    run_doc.status = "Queued"
    run_doc.save(ignore_permissions=True)
    frappe.db.commit()

    frappe.enqueue(
        _RUN_PIPELINE, queue="default", timeout=1800, run_name=run_name, resume_from=step_id
    )
    return run_name


@whitelist()
def launch_options() -> Dict:
    """Option lists for the konsol-exec launch form (so the 4 fields are
    dropdowns, not free text).

    Returns ``{definitions, fiscal_years, fiscal_periods, scopes}``. Pipeline
    definitions, fiscal periods, and scopes come from doctypes; fiscal years are
    the distinct years present in ``epm_gold.gold_trial_balance`` (best-effort —
    an empty list when ClickHouse is unreachable, and the SPA falls back to a
    generated recent-year range). Each scope/period option is ``{value, label}``;
    an empty selection means "all / default" (the params builder omits blanks).
    """
    import frappe

    definitions = [d.name for d in frappe.get_all("Pipeline Definition", order_by="name")]

    fiscal_periods = []
    for f in frappe.get_all(
        "Fiscal Period", fields=["fiscal_period", "label", "quarter"], order_by="fiscal_period"
    ):
        label = f.get("label") or f"Period {f.fiscal_period}"
        if f.get("quarter") and f.quarter != label:
            label = f"{label} · {f.quarter}"
        fiscal_periods.append({"value": str(f.fiscal_period), "label": label})

    scopes = []
    for g in frappe.get_all(
        "Consolidation Group",
        fields=["consolidation_group", "is_group", "data_area_id", "entity_name"],
        order_by="is_group desc, name",
    ):
        if g.get("is_group") and g.get("consolidation_group"):
            scopes.append(
                {"value": g.consolidation_group, "label": f"{g.consolidation_group} (group)"}
            )
        elif g.get("data_area_id"):
            label = g.data_area_id + (f" — {g.entity_name}" if g.get("entity_name") else "")
            scopes.append({"value": g.data_area_id, "label": label})

    fiscal_years = []
    try:
        from konsol import clickhouse

        text = clickhouse.execute(
            "SELECT DISTINCT fiscal_year FROM epm_gold.gold_trial_balance "
            "WHERE fiscal_year > 0 ORDER BY fiscal_year DESC"
        )
        fiscal_years = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    except Exception:  # pragma: no cover - ClickHouse optional
        fiscal_years = []

    return {
        "definitions": definitions,
        "fiscal_years": fiscal_years,
        "fiscal_periods": fiscal_periods,
        "scopes": scopes,
    }


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

    from konsol.schema_lifecycle import check_epm_admin

    check_epm_admin()
    run_doc = frappe.get_doc("Pipeline Run", run_name)
    run_doc.status = "Cancelled"
    run_doc.save(ignore_permissions=True)
    frappe.db.commit()
    return run_name

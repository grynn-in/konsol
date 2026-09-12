"""Stale-run reaper (#67 fix 2).

The single-flight guard (:func:`konsol.orchestrator.api._assert_no_active_run`)
refuses a new run while any Pipeline Run is in an ACTIVE_RUN_STATES status. If a
worker dies mid-run (OOM kill, container restart) the run stays "Running"
forever and wedges the guard permanently — no future run can start. This sweep
releases such runs by marking them ``Failed``.

Staleness is judged off the run's **heartbeat** (#70): an orchestrator-owned
``heartbeat_at`` that :class:`konsol.orchestrator.run.FrappeSink` bumps on every
step start/result, so it advances only on genuine pipeline progress — not on
incidental row touches (a concurrent cancel, a reaper write) that bump
``modified``. :func:`run_heartbeat` resolves which timestamp to use (heartbeat,
else ``modified``/``started_at`` for legacy runs).

The pure helpers :func:`is_run_stale` / :func:`run_heartbeat` are unit-tested on
the host (no frappe); the frappe-bound :func:`reap_stale_runs` is unit-tested
with frappe mocked and exercised in a bench smoke test.
"""
from __future__ import annotations

from datetime import datetime, timedelta

# GENEROUS by design. A single dbt step (e.g. a full consolidation build) can run
# for many minutes without the orchestrator persisting progress / bumping the
# run's ``modified`` — the sink only flushes between per-step start/result hooks,
# so a long-running step looks "idle" to this reaper. The timeout MUST exceed the
# longest expected single step, otherwise the reaper would kill a healthy run
# mid-flight. 120 minutes comfortably clears that while still releasing a truly
# dead worker within a couple of hours.
STALE_RUN_TIMEOUT_MINUTES = 120


def _as_dt(value):
    """Coerce a frappe datetime field (datetime or string) to ``datetime``."""
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S"):
            try:
                return datetime.strptime(value, fmt)
            except ValueError:
                continue
    return None


def is_run_stale(modified, now, timeout_minutes: int = STALE_RUN_TIMEOUT_MINUTES) -> bool:
    """Return True if a run last touched at ``modified`` is older than the timeout.

    Pure: ``modified`` / ``now`` may be ``datetime`` objects or frappe datetime
    strings. Unparseable / missing timestamps return ``False`` (never reap what
    we can't reason about).
    """
    m = _as_dt(modified)
    n = _as_dt(now)
    if m is None or n is None:
        return False
    return (n - m) > timedelta(minutes=timeout_minutes)


def run_heartbeat(run):
    """Pick the timestamp that best reflects a run's last *real* progress (#70).

    Pure. ``run`` may be a dict or a frappe doc. Prefers, in order:

    1. ``heartbeat_at`` — the orchestrator-owned beat bumped by
       :class:`konsol.orchestrator.run.FrappeSink` on every step start/result, so
       it advances only on genuine pipeline progress;
    2. ``modified`` — the incidental row-touch time (a cancel, a reaper write, or
       any unrelated ``save`` also bumps it). Used as a backward-compatible
       fallback for runs that predate the heartbeat field;
    3. ``started_at`` — last resort for a run that hasn't beat yet.

    Returns ``None`` when none is set (``is_run_stale`` then declines to reap).

    Why a dedicated heartbeat instead of ``modified``: ``modified`` is bumped by
    bookkeeping writes that aren't pipeline progress (e.g. a concurrent cancel),
    which would spuriously *reset* the staleness clock and let a wedged worker
    evade the reaper. ``heartbeat_at`` is written *only* at step boundaries, so
    staleness tracks the pipeline, not the row.
    """
    def _get(key):
        return run.get(key) if isinstance(run, dict) else getattr(run, key, None)

    for key in ("heartbeat_at", "modified", "started_at"):
        val = _get(key)
        if val:
            return val
    return None


def reap_stale_runs():
    """Scheduled: mark long-stuck active Pipeline Runs as Failed.

    Frappe-bound. Finds runs in ACTIVE_RUN_STATES whose liveness timestamp
    (``run_heartbeat``: ``heartbeat_at`` → ``modified`` → ``started_at``) is
    older than :data:`STALE_RUN_TIMEOUT_MINUTES` and stamps them ``Failed`` (with
    a note in ``error_log``) via ``set_value`` (no optimistic-lock save) so they
    stop wedging the single-flight guard. Returns the list of reaped run names.
    """
    import frappe

    from konsol.orchestrator.api import ACTIVE_RUN_STATES

    now = frappe.utils.now_datetime()
    candidates = frappe.get_all(
        "Pipeline Run",
        filters={"status": ["in", list(ACTIVE_RUN_STATES)]},
        fields=["name", "heartbeat_at", "modified", "started_at"],
    )
    reaped = []
    note = (
        f"[reaper] marked Failed: no heartbeat for >{STALE_RUN_TIMEOUT_MINUTES}m "
        "(presumed dead worker)"
    )
    for c in candidates:
        # #70: judge staleness on the orchestrator heartbeat (genuine step
        # progress), falling back to modified/started_at for legacy runs.
        if not is_run_stale(run_heartbeat(c), now, STALE_RUN_TIMEOUT_MINUTES):
            continue
        prev = frappe.db.get_value("Pipeline Run", c["name"], "error_log") or ""
        frappe.db.set_value(
            "Pipeline Run",
            c["name"],
            {"status": "Failed", "error_log": (prev + "\n" + note).strip()},
            update_modified=False,
        )
        reaped.append(c["name"])
    if reaped:
        frappe.db.commit()
        frappe.logger().info(
            f"Pipeline Run reaper: marked {len(reaped)} stale run(s) Failed: {reaped}"
        )
    return reaped


# A Build Approval's job (tasks.run_governed_build) runs dbt under a 300 s
# subprocess timeout inside a 600 s RQ job, so a live build can't stay Running
# anywhere near this long, and on a working queue an Approved build starts
# within seconds (#125).
STALE_BUILD_APPROVAL_MINUTES = 30


def stale_build_approval_reason(row, now, timeout_minutes: int = STALE_BUILD_APPROVAL_MINUTES):
    """Why a Build Approval is stuck, or ``None``. Pure.

    - Approved with no ``started_at``: its build job was lost (a worker
      restart, a redeploy, a Redis flush). The clock is ``modified``, the
      approval.
    - Running: the worker died mid-build. The clock is ``started_at``.

    Either one blocks every later build request for its scope, because the
    debounce counts both as in flight.
    """
    def _get(key):
        return row.get(key) if isinstance(row, dict) else getattr(row, key, None)

    state = _get("workflow_state")
    if state == "Approved" and not _get("started_at"):
        if is_run_stale(_get("modified"), now, timeout_minutes):
            return f"approved but never started for >{timeout_minutes}m (its build job was lost)"
    elif state == "Running":
        if is_run_stale(_get("started_at") or _get("modified"), now, timeout_minutes):
            return f"running for >{timeout_minutes}m, past the build job's timeout (presumed dead worker)"
    return None


def _build_job_waiting(name):
    """True if the approval's build job is still queued or started in RQ.
    A build can wait behind a 30-minute pipeline run, so age alone can't tell a
    backlog from a lost job. If RQ can't be asked, assume it is waiting: never
    reap what we can't reason about."""
    from frappe.utils.background_jobs import is_job_enqueued

    from konsol.pipeline.doctype.build_approval.build_approval import governed_build_job_id

    try:
        return is_job_enqueued(governed_build_job_id(name))
    except Exception:
        return True


def reap_stale_build_approvals():
    """Scheduled: mark stuck Build Approvals Failed so their scope can build again (#125).

    The UPDATE re-checks the state it read, so a job that started meanwhile
    is never overwritten, and bumps ``modified``, so a job that loaded the row
    earlier fails its save on the timestamp check instead of writing Running
    over Failed. An approval reaped from Running also has its governed
    Pipeline Run failed: left active, it would block every new build in
    ``_assert_no_active_run`` until reap_stale_runs caught it (120 min).
    Returns the list of reaped names.
    """
    from konsol.orchestrator.api import ACTIVE_RUN_STATES
    import frappe

    now = frappe.utils.now_datetime()
    rows = frappe.get_all(
        "Build Approval",
        filters={"workflow_state": ["in", ["Approved", "Running"]]},
        fields=["name", "workflow_state", "started_at", "modified", "error_message"],
    )
    reaped = []
    for row in rows:
        reason = stale_build_approval_reason(row, now)
        if not reason:
            continue
        if row["workflow_state"] == "Approved" and _build_job_waiting(row["name"]):
            continue  # a backlog, not a lost job: one worker serves every queue
        note = (f"{row.get('error_message') or ''}\n[reaper] marked Failed: {reason}").strip()
        frappe.db.sql(
            """UPDATE `tabBuild Approval`
               SET workflow_state = 'Failed', error_message = %s, completed_at = %s, modified = %s
               WHERE name = %s AND workflow_state = %s""",
            (note, now, now, row["name"], row["workflow_state"]),
        )
        # The UPDATE matched nothing if the row moved on meanwhile.
        if frappe.db.get_value("Build Approval", row["name"], "error_message") != note:
            continue
        if row["workflow_state"] == "Running":
            frappe.db.sql(
                """UPDATE `tabPipeline Run`
                   SET status = 'Failed', completed_at = %s, modified = %s,
                       error_log = TRIM(CONCAT(IFNULL(error_log, ''), '\n', %s))
                   WHERE build_approval = %s AND status IN %s""",
                (now, now, f"[reaper] Build Approval {row['name']} reaped: {reason}", row["name"],
                 tuple(ACTIVE_RUN_STATES)),
            )
        reaped.append(row["name"])
    if reaped:
        frappe.db.commit()
        frappe.logger().warning(f"Build Approval reaper: marked {len(reaped)} stuck approval(s) Failed: {reaped}")
    return reaped


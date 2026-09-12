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
        fields=["name", "workflow_state", "started_at", "modified", "error_message", "build_scope", "rebuild_requested"],
    )
    reaped, follow_ups = [], []
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
        # Read after our own UPDATE, which saw the latest row: a flag set since
        # the get_all above is visible here (#139 review). A row that moved on
        # meanwhile didn't match, and still carries its own error_message.
        after = frappe.db.get_value("Build Approval", row["name"], ["error_message", "rebuild_requested"], as_dict=True)
        if after.error_message != note:
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
        if after.rebuild_requested:
            follow_ups.append(row)
    if reaped:
        frappe.db.commit()
        frappe.logger().warning(f"Build Approval reaper: marked {len(reaped)} stuck approval(s) Failed: {reaped}")
    # A change arrived while a dead build was running (#129): it never reached
    # gold, so request the build it was promised. After the commit above.
    for row in follow_ups:
        try:
            from konsol.build_lock import lock_build_requests
            from konsol.tasks import request_build_for_scope

            # Spend the flag in the request's commit, so the failed-start sweep
            # can't request a second follow-up for this row (#140 review). The
            # build lock first: the debounce's lock order.
            lock_build_requests()
            frappe.db.sql("UPDATE `tabBuild Approval` SET rebuild_requested = 0 WHERE name = %s", row["name"])
            request_build_for_scope(row["build_scope"], "Build Approval", row["name"], carries_changes=True)
        except Exception:
            frappe.db.rollback()
            frappe.log_error(title=f"Follow-up build request for {row['name']} failed")
            frappe.db.commit()
    # Scheduled with the reaper rather than as its own entry: a new scheduler
    # hook needs a migrate to register, and this belongs to the same sweep.
    try:
        follow_up_failed_starts()
    except Exception:
        frappe.db.rollback()
        frappe.log_error(title="Build Approval follow-up sweep failed")
    return reaped


# tasks.run_governed_build prefixes a start failure's error_message with this.
# The sweep below keys on it: other Failed rows with the flag (a build that ran,
# a reaped one) have already requested their follow-up. A reset to Draft clears
# the message, so a row that runs again can't carry an old one.
START_FAILURE_PREFIX = "Governed build could not start"

# Follow-ups the sweep requests for one chain of start failures before it gives
# up and logs an error (#140 review). A follow-up is requested once the queue
# looks idle, but its job can still line up behind a run started in the same
# scheduler tick and fail to start for the same reason; a few retries cover
# that, and a start that keeps failing stops here.
START_FAILURE_RETRIES = 3


def _get(row, key):
    return row.get(key) if isinstance(row, dict) else getattr(row, key, None)


def failed_to_start(row):
    """True if ``row`` is a Build Approval whose build never started. Pure."""
    return bool(
        _get(row, "workflow_state") == "Failed"
        and not _get(row, "started_at")
        and (_get(row, "error_message") or "").startswith(START_FAILURE_PREFIX)
    )


def start_failure_follow_up(row, follow_ups_so_far):
    """What a Build Approval that failed to start is owed (#140). Pure.

    ``"request"``: it holds changes it absorbed (the flag) and its chain has
    had fewer than START_FAILURE_RETRIES follow-ups. ``"give_up"``: the chain
    is spent. ``None``: nothing is owed. ``follow_ups_so_far`` comes from
    :func:`start_failure_chain_length`.
    """
    if not (failed_to_start(row) and _get(row, "rebuild_requested")):
        return None
    return "give_up" if follow_ups_so_far >= START_FAILURE_RETRIES else "request"


def start_failure_chain_length(name, lookup):
    """How many follow-ups have already been requested in ``name``'s chain of
    start failures. Pure: ``lookup(name)`` returns the row (with
    ``trigger_doctype`` / ``trigger_docname``) or None.

    Derived from the trigger links, not stored: a follow-up's trigger is the
    Build Approval it follows up. Walk up while the parent also failed to
    start; a parent that built (or never existed) ends the chain. No new
    field, so no migrate.
    """
    count, seen = 0, {name}
    row = lookup(name)
    while row and _get(row, "trigger_doctype") == "Build Approval":
        parent_name = _get(row, "trigger_docname")
        if not parent_name or parent_name in seen:
            break
        parent = lookup(parent_name)
        if not parent or not failed_to_start(parent):
            break
        count += 1
        seen.add(parent_name)
        row = parent
    return count


def _build_queue_busy():
    """A Pipeline Run is active, or a Build Approval is about to build or building.

    The usual start failure is an active run (``_assert_no_active_run``): a
    request made then fails the same way at once, and a follow-up auto-approved
    next to another approval collides with it. So the sweep waits for idle."""
    import frappe

    from konsol.orchestrator.api import ACTIVE_RUN_STATES

    return bool(
        frappe.get_all("Pipeline Run", filters={"status": ["in", list(ACTIVE_RUN_STATES)]}, limit=1)
        or frappe.get_all("Build Approval", filters={"workflow_state": ["in", ["Approved", "Running"]]}, limit=1)
    )


_CHAIN_FIELDS = ["name", "workflow_state", "started_at", "error_message", "trigger_doctype", "trigger_docname"]


def follow_up_failed_starts():
    """Request the build that each start-failed, flagged Build Approval owes (#140).

    The follow-up is flagged too (``carries_changes``), so if it also fails to
    start, the next sweep retries it, up to START_FAILURE_RETRIES follow-ups
    per chain; then the sweep logs an error and stops. Each claim clears the
    flag in the transaction that requests the follow-up, on every flagged
    start-failed row of the scope (one build covers them all), so a second
    sweep finds nothing. Nothing is requested while the build queue is busy,
    and that is re-read before each row, so one sweep never lines up two
    builds against each other. Lock order is the debounce's: the build lock,
    then the approval rows. Returns the names followed up.
    """
    import frappe

    from konsol.build_lock import lock_build_requests
    from konsol.tasks import request_build_for_scope

    def lookup(name):
        return frappe.db.get_value("Build Approval", name, _CHAIN_FIELDS, as_dict=True)

    rows = frappe.get_all(
        "Build Approval",
        filters={"workflow_state": "Failed", "rebuild_requested": 1, "started_at": ["is", "not set"],
                 "error_message": ["like", f"{START_FAILURE_PREFIX}%"]},
        fields=["name", "workflow_state", "rebuild_requested", "started_at", "error_message", "build_scope"],
        order_by="creation asc",
    )
    followed_up = []
    for row in rows:
        if _build_queue_busy():
            break  # the next sweep picks up the rest
        so_far = start_failure_chain_length(row["name"], lookup)
        decision = start_failure_follow_up(row, so_far)
        if not decision:
            continue
        try:
            lock_build_requests()
            # Claim under the lock, re-reading the latest rows: a second sweep
            # finds the flags cleared, and a row that moved on isn't taken.
            claimed = [r[0] for r in frappe.db.sql(
                """SELECT name FROM `tabBuild Approval`
                   WHERE build_scope = %s AND workflow_state = 'Failed' AND rebuild_requested = 1
                     AND started_at IS NULL AND error_message LIKE %s
                   FOR UPDATE""",
                (row["build_scope"], f"{START_FAILURE_PREFIX}%"),
            )]
            if row["name"] not in claimed:
                frappe.db.rollback()
                continue
            if decision == "give_up":
                frappe.db.sql("UPDATE `tabBuild Approval` SET rebuild_requested = 0 WHERE name = %s", row["name"])
                frappe.log_error(
                    title=f"Build Approval {row['name']}: gave up after {so_far} follow-up builds failed to start",
                    message=(f"Changes absorbed by the '{row['build_scope']}' build were never built: {so_far} "
                             f"follow-ups in a row failed to start ({row['error_message']}). Request a "
                             "build of this scope once the cause is fixed (#140)."),
                )
                frappe.db.commit()
                continue
            frappe.db.sql("UPDATE `tabBuild Approval` SET rebuild_requested = 0 WHERE name IN %s", (tuple(claimed),))
            # Commits the clear with the new request, or absorbs into (and
            # flags) a pending build for the scope, which carries it forward.
            request_build_for_scope(row["build_scope"], "Build Approval", row["name"], carries_changes=True)
            followed_up.append(row["name"])
        except Exception:
            frappe.db.rollback()  # the flags stay: the next sweep retries
            frappe.log_error(title=f"Follow-up build request for {row['name']} failed")
            frappe.db.commit()
    if followed_up:
        frappe.logger().info(f"Build Approval follow-ups for failed starts: {followed_up}")
    return followed_up


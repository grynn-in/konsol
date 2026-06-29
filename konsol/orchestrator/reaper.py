"""Stale-run reaper (#67 fix 2).

The single-flight guard (:func:`konsol.orchestrator.api._assert_no_active_run`)
refuses a new run while any Pipeline Run is in an ACTIVE_RUN_STATES status. If a
worker dies mid-run (OOM kill, container restart) the run stays "Running"
forever and wedges the guard permanently — no future run can start. This sweep
releases such runs by marking them ``Failed``.

The pure timestamp-math helper :func:`is_run_stale` is unit-tested on the host
(no frappe); the frappe-bound :func:`reap_stale_runs` is wired to
``scheduler_events`` and exercised in a bench smoke test.
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


def reap_stale_runs():
    """Scheduled: mark long-stuck active Pipeline Runs as Failed.

    Frappe-bound. Finds runs in ACTIVE_RUN_STATES whose ``modified`` is older
    than :data:`STALE_RUN_TIMEOUT_MINUTES` and stamps them ``Failed`` (with a
    note in ``error_log``) via ``set_value`` (no optimistic-lock save) so they
    stop wedging the single-flight guard. Returns the list of reaped run names.
    """
    import frappe

    from konsol.orchestrator.api import ACTIVE_RUN_STATES

    now = frappe.utils.now_datetime()
    candidates = frappe.get_all(
        "Pipeline Run",
        filters={"status": ["in", list(ACTIVE_RUN_STATES)]},
        fields=["name", "modified"],
    )
    reaped = []
    note = (
        f"[reaper] marked Failed: no progress for >{STALE_RUN_TIMEOUT_MINUTES}m "
        "(presumed dead worker)"
    )
    for c in candidates:
        if not is_run_stale(c.get("modified"), now, STALE_RUN_TIMEOUT_MINUTES):
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

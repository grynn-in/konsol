"""Minimal 5-field cron matcher + scheduler entrypoint (PRD-14).

Pure-python core (:func:`is_due`) — no top-level ``frappe`` import, so it
runs on the host under ``pytest``. The frappe-bound :func:`run_due_schedules`
imports frappe **inside the function** and is exercised only in a bench /
container smoke test.

The matcher supports the standard 5 fields ``minute hour dom month dow`` with
``*``, ``*/n``, ``a-b``, ``a,b,c`` and bare literals. No ``@macro`` shorthands
(not needed for the Pipeline Schedule UI). Day-of-week accepts both ``0`` and
``7`` for Sunday; internally we normalise Python's ``Monday==0`` to cron's
``Sunday==0``.
"""

# Inclusive (low, high) bounds for each cron field, in field order.
_BOUNDS = (
    (0, 59),  # minute
    (0, 23),  # hour
    (1, 31),  # day of month
    (1, 12),  # month
    (0, 7),   # day of week (0 and 7 both == Sunday)
)


def _parse_field(token, low, high):
    """Expand one cron field token into the set of matching integers."""
    values = set()
    for part in token.split(","):
        part = part.strip()
        if not part:
            continue
        step = 1
        if "/" in part:
            base, _, step_s = part.partition("/")
            step = int(step_s)
            if step <= 0:
                raise ValueError(f"invalid cron step {part!r}")
        else:
            base = part

        if base == "*":
            start, end = low, high
        elif "-" in base:
            start_s, _, end_s = base.partition("-")
            start, end = int(start_s), int(end_s)
        else:
            start = end = int(base)

        if start < low or end > high or start > end:
            raise ValueError(f"cron field {part!r} out of range [{low},{high}]")

        values.update(range(start, end + 1, step))
    if not values:
        raise ValueError(f"empty cron field {token!r}")
    return values


def is_due(expr, now, last_run=None):
    """Return ``True`` if the cron ``expr`` matches the ``now`` datetime.

    ``last_run`` guards against a double-fire within the same minute: if the
    schedule already ran in the minute ``now`` falls in, this returns ``False``.
    """
    fields = expr.split()
    if len(fields) != 5:
        raise ValueError(
            f"cron expression must have 5 fields, got {len(fields)}: {expr!r}"
        )

    minute_f, hour_f, dom_f, month_f, dow_f = (
        _parse_field(tok, lo, hi) for tok, (lo, hi) in zip(fields, _BOUNDS)
    )

    if now.minute not in minute_f:
        return False
    if now.hour not in hour_f:
        return False
    if now.month not in month_f:
        return False
    if now.day not in dom_f:
        return False

    # Python weekday(): Monday==0 .. Sunday==6 → cron dow Sunday==0 .. Saturday==6
    cron_dow = (now.weekday() + 1) % 7
    # Field may carry 7 for Sunday; treat 7 as 0 when matching.
    dow_set = {0 if d == 7 else d for d in dow_f}
    if cron_dow not in dow_set:
        return False

    if last_run is not None:
        # Same calendar minute as the last run → already fired, don't re-fire.
        if (
            last_run.year == now.year
            and last_run.month == now.month
            and last_run.day == now.day
            and last_run.hour == now.hour
            and last_run.minute == now.minute
        ):
            return False

    return True


def run_due_schedules():
    """Enqueue every enabled Pipeline Schedule whose cron is due now.

    Frappe-bound: invoked once a minute by ``scheduler_events.cron``. For each
    due schedule it starts a run via the PRD-10 API and stamps ``last_run``.
    """
    import frappe

    from konsol.orchestrator import api

    now = frappe.utils.now_datetime()
    schedules = frappe.get_all(
        "Pipeline Schedule",
        filters={"enabled": 1},
        fields=["name", "pipeline_definition", "cron", "params", "last_run"],
    )
    for sched in schedules:
        cron_expr = sched.get("cron")
        if not cron_expr:
            continue
        try:
            due = is_due(cron_expr, now, last_run=sched.get("last_run"))
        except ValueError:
            frappe.log_error(
                f"Invalid cron {cron_expr!r} on Pipeline Schedule {sched['name']}",
                "run_due_schedules",
            )
            continue
        if not due:
            continue
        api.start_run(sched.get("pipeline_definition"), sched.get("params"))
        frappe.db.set_value(
            "Pipeline Schedule", sched["name"], "last_run", now, update_modified=False
        )
    frappe.db.commit()

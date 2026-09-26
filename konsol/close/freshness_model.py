"""Are the consolidated numbers current? Pure (konsol#305 A05; stories 0.2, 7.4).

Imports nothing from frappe or konsol; the caller passes every input:

- ``builds``: Build Approval rows as dicts with ``name``, ``build_scope``,
  ``workflow_state``, ``completed_at`` and ``error_message``.
- ``input_changes``: dicts with ``doctype`` and ``modified``, one per changed
  record (or the latest per doctype).
- ``scope_of``: doctype -> build scope, from ``tasks.DOCTYPE_BUILD_MAP``.
- ``flagged_states``: ``build_lock.FLAGGED_STATES``.

Rules:

- A build is successful only when ``workflow_state == "Completed"``;
  ``completed_at`` is written on failure too (tasks.py:399).
- A change is covered by a Completed ``consolidation`` or ``full`` build whose
  ``completed_at`` is at or after the change, whatever the doctype's own scope
  (A05b: a ``staging`` build rebuilds gold_consolidation_adjustments but not
  gold_fully_consolidated_tb, measured from the dbt manifest 25 Sep 2026).
  ``scope_of`` still names every doctype that may be passed.
- ``as_of`` is the latest Completed ``completed_at`` over the scopes that
  reach the consolidated numbers: ``consolidation`` and ``full``.
- ``last_failed`` is set when the latest terminal (Completed or Failed)
  consolidation/full build is Failed.
- ``state`` precedence, first match wins: ``pending`` (a build is in a flagged
  state), ``failed``, ``never_built`` (no Completed consolidation/full build),
  ``stale`` (``changed_since`` is not empty), ``fresh``. ``changed_since``,
  ``pending`` and ``last_failed`` are reported whatever the state.

Nothing is skipped silently: a changed doctype with no declared scope, or a
terminal build with no ``completed_at``, raises ValueError.
"""

COMPLETED = "Completed"
FAILED = "Failed"
FULL = "full"
NUMBERS_SCOPES = ("consolidation", FULL)


def _completed_at(build):
    at = build.get("completed_at")
    if at is None:
        raise ValueError(
            f"Build Approval {build.get('name')} is {build.get('workflow_state')} "
            "but has no completed_at; set it before freshness can be judged."
        )
    return at


def freshness(builds, input_changes, scope_of, flagged_states):
    builds = list(builds)
    flagged = set(flagged_states)

    pending = sum(1 for b in builds if b.get("workflow_state") in flagged)

    # latest Completed completed_at per scope
    last_ok = {}
    terminal_numbers = []
    for b in builds:
        state = b.get("workflow_state")
        if state not in (COMPLETED, FAILED):
            continue
        at = _completed_at(b)
        scope = b.get("build_scope")
        if state == COMPLETED and (scope not in last_ok or at > last_ok[scope]):
            last_ok[scope] = at
        if scope in NUMBERS_SCOPES:
            terminal_numbers.append((at, state == COMPLETED, b))

    ok_numbers = [last_ok[s] for s in NUMBERS_SCOPES if s in last_ok]
    as_of = max(ok_numbers) if ok_numbers else None

    last_failed = None
    if terminal_numbers:
        # On a tie, a Completed build wins over a Failed one.
        at, ok, latest = max(terminal_numbers, key=lambda t: (t[0], t[1]))
        if not ok:
            reason = (latest.get("error_message") or "").strip()
            last_failed = {
                "name": latest.get("name"),
                "at": at,
                "reason": reason or f"No error recorded on {latest.get('name')}",
            }

    changed = set()
    for change in input_changes:
        dt = change["doctype"]
        if dt not in scope_of:
            raise ValueError(
                f"{dt} has no build scope declared; add it to tasks.DOCTYPE_BUILD_MAP "
                "so its changes can be judged."
            )
        covering = [last_ok[s] for s in NUMBERS_SCOPES if s in last_ok]
        if not covering or change["modified"] > max(covering):
            changed.add(dt)
    changed_since = sorted(changed)

    if pending:
        state = "pending"
    elif last_failed:
        state = "failed"
    elif as_of is None:
        state = "never_built"
    elif changed_since:
        state = "stale"
    else:
        state = "fresh"

    return {
        "state": state,
        "as_of": as_of,
        "pending": pending,
        "changed_since": changed_since,
        "last_failed": last_failed,
    }

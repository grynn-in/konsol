"""Background jobs: governed dbt builds + Airbyte pipeline.

Build governance: doc saves create Build Approvals instead of
firing raw `dbt build`. Scopes map to dbt domain tags for selective builds.
"""
import os
import subprocess
import time

import frappe

from konsol.airbyte_service import AirbyteClient
from konsol.build_command import dbt_build_command
# reaper.py imports nothing from konsol at module level, so this can't cycle.
from konsol.orchestrator.reaper import START_FAILURE_PREFIX


def _dbt_bin():
    """Absolute path to the bench-venv dbt binary.

    Web/worker processes don't have env/bin on PATH, so a bare `dbt` raises
    FileNotFoundError. Fall back to `dbt` only if the venv copy is absent.
    """
    candidate = os.path.join(frappe.utils.get_bench_path(), "env", "bin", "dbt")
    return candidate if os.path.exists(candidate) else "dbt"


# ---------------------------------------------------------------------------
# Build scope mapping: doctype → (scope, risk)
# ---------------------------------------------------------------------------
# All 9 trigger doctypes map to "staging" (low risk) by default.
# Full/actuals/consolidation rebuilds require manual Build Approval.
DOCTYPE_BUILD_MAP = {
    "Consolidation Group": {"scope": "staging", "risk": "low"},
    "Consolidation Adjustment": {"scope": "staging", "risk": "low"},
    "Ownership Period": {"scope": "staging", "risk": "low"},
    "Historical Equity Rate": {"scope": "staging", "risk": "low"},
    "IC Elimination Rule": {"scope": "staging", "risk": "low"},
    "IC Balance": {"scope": "staging", "risk": "low"},
    "Allocation Rule": {"scope": "staging", "risk": "low"},
    "Allocation Driver": {"scope": "staging", "risk": "low"},
    "Allocation Run": {"scope": "staging", "risk": "low"},
    # konsol#110: consolidation, not staging — `staging` selects five models,
    # none of which read the entity registry; `+tag:domain:consolidation`
    # reaches silver_entity_currencies and gold_consolidated_trial_balance.
    # Requested from Entity's controller, not from doc_events.
    "Entity": {"scope": "consolidation", "risk": "high"},
    # F8: a submitted trial balance must reach gold, and a cancelled one must
    # leave it. Mapped only now that the trigger runs after the commit
    # (konsol#126); consolidation reaches bronze_trial_balance_submissions ->
    # silver_gl_entries -> gold_trial_balance -> the consolidated models.
    "Trial Balance Submission": {"scope": "consolidation", "risk": "high"},
    # konsol#103: the governed translation rates. `staging` would not reach
    # gold_consolidated_trial_balance, which is what reads them.
    "Group Exchange Rate": {"scope": "consolidation", "risk": "high"},
}

# Scope → dbt selector. Kept as the fallback/default; the Build Scope doctype
# is the runtime source of truth (see _scope_selector / _raw_dependent_scopes).
SCOPE_SELECTOR = {
    "staging": "tag:domain:staging",
    "actuals": "tag:domain:actuals",
    "scenarios": "tag:domain:scenarios",
    "consolidation": "+tag:domain:consolidation",
    "reporting": "+tag:domain:reporting",
    # konsol#182: the group chart, everything it classifies, and everything
    # those read (@). Not +tag:domain:consolidation, which would leave the
    # balance sheet, P&L and variance models downstream of silver_main_accounts
    # stale; not silver_main_accounts+ alone, which fails on a site that has
    # never built (its models read bronze, the period and reporting
    # hierarchies). @ reaches ERP staging/bronze models fed by epm_raw, so an
    # enabled connector that never synced or is Failed/Running still blocks
    # it (see _check_chart_build_allowed). It is not in RAW_DEPENDENT_SCOPES
    # because it carries no trial-balance-rows requirement — a TB-only site
    # must be able to build its chart before any TB exists.
    "chart": "@silver_main_accounts",
    "full": None,  # no selector = full build
}

# Scopes that require epm_raw data (fallback; see _raw_dependent_scopes).
RAW_DEPENDENT_SCOPES = {"actuals", "scenarios", "consolidation", "reporting", "full"}


def _known_domains():
    """Per-model build domains. Prefers the Build Scope doctype; falls back to
    the hardcoded SCOPE_SELECTOR domains (excluding the special 'full' scope)."""
    try:
        if frappe.db.table_exists("Build Scope"):
            names = frappe.get_all("Build Scope", pluck="name")
            if names:
                return set(names)
    except Exception:
        pass
    return {s for s, sel in SCOPE_SELECTOR.items() if sel is not None}


def _scope_selector(scope):
    """dbt --select for a build scope; None for 'full' or an unknown scope
    (no selector → full build), preserving the original SCOPE_SELECTOR semantics."""
    if scope not in _known_domains():
        return None
    # SCOPE_SELECTOR carries upstream deps (e.g. '+tag:domain:reporting').
    return SCOPE_SELECTOR.get(scope) or f"tag:domain:{scope}"


def _raw_dependent_scopes():
    """Scopes that require epm_raw. Prefers Build Scope docs flagged
    requires_raw_data=1 (plus the 'full' build); falls back to RAW_DEPENDENT_SCOPES."""
    try:
        if frappe.db.table_exists("Build Scope"):
            rows = frappe.get_all(
                "Build Scope", filters={"requires_raw_data": 1}, pluck="name")
            if rows:
                return set(rows) | {"full"}
    except Exception:
        pass
    return set(RAW_DEPENDENT_SCOPES)


# ---------------------------------------------------------------------------
# Preflight check
# ---------------------------------------------------------------------------
def _preflight_check(build_scope):
    """Validate preconditions before running a build.

    Returns (ok: bool, message: str).
    - staging scope always passes (no epm_raw dependency)
    - Raw-dependent scopes check Airbyte sync status in EPM Settings
    """
    from konsol.clickhouse import check_health

    # Check ClickHouse connectivity
    ch_status = check_health()
    if ch_status["status"] != "healthy":
        return False, f"ClickHouse unhealthy: {ch_status}"

    # Staging doesn't need raw data
    if build_scope == "staging":
        return True, "Staging scope — no raw data dependency"

    # chart builds ERP staging/bronze models from epm_raw (konsol#182) but
    # carries no trial-balance-rows requirement — gate on connector sync
    # status only, not the full check_raw_data_available fallback chain.
    if build_scope == "chart":
        return _check_chart_build_allowed()

    # Raw-dependent scopes check Airbyte sync status
    if build_scope in _raw_dependent_scopes():
        return check_raw_data_available()

    return True, "OK"


def _trial_balance_rows():
    """The trial balance rows bronze reads: epm_raw.trial_balance_submissions
    whose batch is claimed in the control table (a cancelled submission's rows
    stay behind unclaimed until reaped). Counted in the warehouse, not from
    MariaDB's docstatus: a ClickHouse volume wiped since the submissions leaves
    the documents and nothing to build from. 0 when it cannot be read (no table
    yet, or unreachable), so the build is refused."""
    from konsol.clickhouse import execute

    try:
        return int(execute(
            "SELECT count() FROM epm_raw.trial_balance_submissions WHERE batch_id IN "
            "(SELECT batch_id FROM epm_raw.trial_balance_submission_control)") or 0)
    except Exception:  # noqa: BLE001
        return 0


def _batches_without_basis():
    """Claimed trial balance batches whose latest claim declares no Amount
    Basis (konsolidat#199). Returns ``(names, count)``: up to five
    ``submission_name``s and the total, so the refusal can name them.

    The control table is ReplacingMergeTree(claimed_at) and Set Amount Basis
    re-claims rather than updates, so the LATEST claim per batch decides:
    ``argMax(amount_basis, claimed_at)`` per ``batch_id``, never an older row
    that ReplacingMergeTree has not merged away yet.

    ``None`` when the control table has no ``amount_basis`` column yet (an
    old stack; ClickHouse reports the missing identifier by name), which the
    caller turns into "run bench migrate" rather than a silently passing
    check. Any OTHER failure (connection refused, timeout, an unrelated
    ClickHouse error) is ``("error", text)`` with the error's first 200
    characters, so the refusal says what actually failed instead of sending
    the operator to migrate a schema that is already current.
    """
    from konsol.clickhouse import execute

    try:
        text = execute(
            "SELECT count(), arrayStringConcat(arraySlice(arraySort(groupArray(submission_name)), 1, 5), ',') "
            "FROM (SELECT batch_id, argMax(submission_name, claimed_at) AS submission_name, "
            "argMax(amount_basis, claimed_at) AS amount_basis "
            "FROM epm_raw.trial_balance_submission_control GROUP BY batch_id) "
            "WHERE amount_basis = ''")
    except Exception as e:  # noqa: BLE001
        message = str(e)
        missing_column = "amount_basis" in message and any(
            marker in message for marker in ("UNKNOWN_IDENTIFIER", "Missing columns", "Unknown identifier"))
        if missing_column:
            return None
        return "error", message[:200]
    # One TSV row: "<count>\t<name,name,…>"; execute() strips a trailing tab,
    # so an empty result arrives as "0".
    parts = (text or "").split("\t")
    count = int(parts[0] or 0)
    names = [n for n in (parts[1] if len(parts) > 1 else "").split(",") if n]
    return names, count


def _connector_sync_gate():
    """Enabled-connector sync-status gate shared by every raw-dependent scope
    and by chart (@silver_main_accounts reaches ERP staging/bronze models fed
    by epm_raw even though chart carries no trial-balance-rows requirement).

    Returns (ok, message) when at least one enabled Connector exists to gate
    on: False if any has never synced or its last sync is Failed/Running,
    naming that connector; True once all are synced OK. Returns None when
    there is no Connector table or no enabled connector, leaving the caller
    to decide what "no connector" means for its scope.
    """
    if not frappe.db.table_exists("Connector"):
        return None

    connectors = frappe.get_all(
        "Connector",
        filters={"enabled": 1},
        fields=["name", "connector_name", "last_sync_status", "last_sync_at"],
        limit_page_length=0,
    )
    if not connectors:
        return None

    for c in connectors:
        if not c.last_sync_at:
            return False, f"Connector '{c.connector_name}' has never synced — epm_raw may be empty"
        if c.last_sync_status in ("Failed", "Running"):
            return False, f"Connector '{c.connector_name}' sync status is '{c.last_sync_status}' — cannot build from raw"
    return True, f"All {len(connectors)} enabled connectors synced OK"


def _check_chart_build_allowed():
    """Preflight for the chart scope (konsol#182): @silver_main_accounts
    builds ERP staging/bronze models from epm_raw, so an enabled connector
    that never synced or is Failed/Running still blocks it — same gate, same
    messages as the raw-dependent scopes. Unlike them, chart carries no
    trial-balance-rows requirement: a TB-only site must be able to build its
    chart before any TB exists, so no enabled connector means pass.

    Returns (ok: bool, message: str).
    """
    if frappe.get_single("EPM Settings").get("skip_airbyte_sync"):
        return True, "Airbyte sync skipped (skip_airbyte_sync enabled) — building from existing epm_raw"

    gate = _connector_sync_gate()
    if gate is not None:
        return gate

    return True, "No enabled connector — chart build has no trial-balance-rows dependency"


def _basis_refusal(rows):
    """``(False, message)`` when claimed trial balance batches exist and any
    lacks an Amount Basis (or the control table has no such column yet), else
    None. Nothing claimed → nothing to declare (an ERP-only site is unaffected).
    konsolidat#199: the warehouse normalises each batch by its declared basis;
    an undeclared batch would still be read as period movements."""
    if not rows:
        return None
    without = _batches_without_basis()
    if without is None:
        return False, "epm_raw.trial_balance_submission_control has no amount_basis column: run bench migrate"
    if without[0] == "error":
        return False, f"could not read the amount bases of the claimed batches: {without[1]}"
    names, n = without
    if n:
        return False, (f"{n} claimed trial balance batch(es) have no Amount Basis (e.g. {', '.join(names)}): "
                       "set it on the submissions (Trial Balance Submission list → Set Amount Basis) "
                       "before building")
    return None


def check_raw_data_available():
    """Check if epm_raw has valid data.

    konsol#182 (decided 13 Sep 2026): trial balance rows in the warehouse ARE
    raw data. The canonical path is a trial balance uploaded to konsol, so a
    site with no enabled connector and landed trial balances builds, with no
    Airbyte sync. A connector that is mid-sync or failed still blocks.

    When connectors are registered, gate on per-connector sync status (an
    enabled connector that has never synced or whose last sync Failed/Running
    blocks the build, and the message names it). Otherwise fall back to the
    global EPM Settings Airbyte sync status — checked BEFORE the trial-balance
    pass, so a feed the Airbyte webhook (api.py) marked Failed or still
    Running blocks the build even with a claimed trial-balance row already in
    the warehouse.

    Returns (ok: bool, message: str).
    """
    # konsolidat#199: claimed trial balance batches must declare their Amount
    # Basis whatever else gates the build — the skip_airbyte_sync short-circuit
    # below is exactly the trial-balance-only site, so this runs first.
    rows = _trial_balance_rows()
    refusal = _basis_refusal(rows)
    if refusal is not None:
        return refusal

    # When Airbyte sync is skipped (demo data / manual epm_raw load), there is
    # no connector or Airbyte status to gate on — readiness is implied by the
    # operator having loaded epm_raw out of band. Short-circuit before any
    # connector/Airbyte gating so a missing/never-synced connector can't block.
    if frappe.get_single("EPM Settings").get("skip_airbyte_sync"):
        return True, "Airbyte sync skipped (skip_airbyte_sync enabled) — building from existing epm_raw"

    gate = _connector_sync_gate()
    if gate is not None:
        return gate

    settings = frappe.get_single("EPM Settings")
    sync_status = settings.last_airbyte_sync_status

    # No enabled connector gates this site, but the Airbyte webhook (api.py
    # ~1564) sets this global status without requiring a Connector doctype or
    # an enabled connector. A build must not run on a feed it marked Failed or
    # still Running just because a trial balance was also submitted — this
    # runs BEFORE the trial-balance pass below.
    if sync_status in ("Failed", "Running"):
        return False, (f"Airbyte sync status is '{sync_status}' — cannot build from raw. If this site no "
                       "longer uses Airbyte, turn on Skip Airbyte Sync in EPM Settings.")

    # Trial balances uploaded to konsol and landed in the warehouse are this
    # site's raw data (konsol#182).
    if rows:
        return True, (f"{rows} trial balance rows in epm_raw.trial_balance_submissions "
                      "— building from them (no connector)")

    if not settings.last_airbyte_sync_at:
        return False, "Airbyte has never synced — epm_raw may be empty"

    return True, f"Airbyte sync OK (status={sync_status}, rows={settings.last_airbyte_sync_rows})"


# ---------------------------------------------------------------------------
# Governed dbt build (called from Build Approval)
# ---------------------------------------------------------------------------
def _create_governed_pipeline_run(build_request_doc):
    """Create a Pipeline Run audit row for a governed PBR execution."""
    run = frappe.get_doc({
        "doctype": "Pipeline Run",
        "status": "Queued",
        "build_approval": build_request_doc.name,
        "triggered_by": build_request_doc.requested_by or frappe.session.user,
        "started_at": frappe.utils.now_datetime(),
    })
    run.insert(ignore_permissions=True)
    frappe.db.commit()
    return run.name


_TERMINAL_RUN_STATES = ("Completed", "Failed", "Cancelled")


def _finalize_governed_pipeline_run(pipeline_run, *, status, dbt_result=None, error_log=None, commit=True):
    """Persist terminal status on the governed Pipeline Run. ``commit=False``
    leaves the commit to the caller, to land with its own write. Returns
    whether the status was applied: False with no run, or when the run is
    already finished and ``status`` would make it active again."""
    if not pipeline_run:
        return False
    # A locking read: the latest row, so the save can't fail its timestamp check.
    doc = frappe.get_doc("Pipeline Run", pipeline_run, for_update=True)
    if doc.status in _TERMINAL_RUN_STATES and status not in _TERMINAL_RUN_STATES:
        # Cancelled (orchestrator cancel_run) or reaped: a job still running
        # must not make it active again (#140).
        return False
    doc.status = status
    if dbt_result is not None:
        doc.dbt_result = dbt_result
    if error_log is not None:
        doc.error_log = error_log
    if status in ("Completed", "Failed"):
        doc.completed_at = frappe.utils.now_datetime()
    doc.save(ignore_permissions=True)
    if commit:
        frappe.db.commit()
    return True


def run_governed_build(build_request):
    """Execute a governed dbt build for a Build Approval.

    Called via frappe.enqueue from BuildApproval.on_update.
    Runs preflight checks, then a selective dbt build (--select on the scope's
    tag, with --indirect-selection cautious; see konsol.build_command).
    """
    doc = frappe.get_doc("Build Approval", build_request)

    # Only an Approved request builds. The reaper (#125) fails an approval whose
    # job looked lost; if that job turns up after all, it must not resurrect a
    # row an operator already sees as Failed.
    if doc.workflow_state != "Approved":
        frappe.logger().warning(
            f"Governed build {build_request} is {doc.workflow_state}, not Approved; not building"
        )
        return

    # #67 fix 5: a governed dbt build shells `dbt` against the SAME shared project
    # dir as an orchestrator run, so the two must not run concurrently (racing
    # target/ + incremental models). Honor the orchestrator single-flight guard
    # here. The check AND the create must be inside the named lock (atomic), else
    # it's a plain TOCTOU against start_run/trigger_pipeline. CRITICAL ordering:
    # check BEFORE _create_governed_pipeline_run(), which itself inserts a "Queued"
    # (active) Pipeline Run — checking after would always see that row and
    # self-block. If blocked (or startup fails), mark this request Failed and
    # re-raise so the job records the failure.
    pipeline_run = None
    try:
        # Inside the try: an import that fails is a start failure too, not a
        # job that dies leaving the row Approved for the reaper (#140).
        from konsol.build_lock import build_writer
        from konsol.orchestrator.api import _assert_no_active_run, single_flight_lock

        with single_flight_lock():
            _assert_no_active_run()
            pipeline_run = _create_governed_pipeline_run(doc)
            doc.workflow_state = "Running"
            doc.started_at = frappe.utils.now_datetime()
            # Starting reads every change absorbed while Approved, so their
            # flag is spent (#140); before_save allows this one clear.
            doc.rebuild_requested = 0
            # As Administrator: the workflow's Start transition (konsol#215).
            with build_writer():
                doc.save(ignore_permissions=True)
            frappe.db.commit()
    except Exception as exc:
        # Drop whatever a failed save half-wrote. Only the Pipeline Run
        # outlives it: _create_governed_pipeline_run committed it.
        frappe.db.rollback()
        message = f"{START_FAILURE_PREFIX}: {exc}"
        doc.reload()
        if pipeline_run:
            # Left Queued, the run would block every build, the follow-up's
            # included, until reap_stale_runs caught it (120 min). Failed in
            # the commit that fails the approval (#140 re-review).
            _finalize_governed_pipeline_run(pipeline_run, status="Failed", error_log=message, commit=False)
        if doc.workflow_state == "Approved":
            # Nothing was read, so a change absorbed while pending keeps its
            # flag (before_save won't clear it): reaper.follow_up_failed_starts
            # requests that build once nothing else is building (#140).
            from konsol.build_lock import build_writer

            doc.workflow_state = "Failed"
            doc.error_message = message
            doc.completed_at = frappe.utils.now_datetime()
            _set_duration(doc)
            with build_writer():  # defensive: this save is Approved -> Failed
                doc.save(ignore_permissions=True)
        else:
            # It moved on while the job loaded it (an operator cancelled or
            # reset it, or the reaper failed it), so it isn't this job's to
            # fail. Leave it in the state it is in: marked a start failure,
            # a Cancelled row would be followed up (#140 re-review).
            frappe.logger().warning(
                f"Governed build {doc.name} could not start ({exc}); it is {doc.workflow_state} now, "
                f"not Approved, so it is left {doc.workflow_state}"
            )
        frappe.db.commit()
        raise

    try:
        # Preflight
        ok, msg = _preflight_check(doc.build_scope)
        doc.preflight_result = msg
        if not ok:
            doc.workflow_state = "Failed"
            doc.error_message = f"Preflight failed: {msg}"
            _finalize_governed_pipeline_run(
                pipeline_run,
                status="Failed",
                error_log=doc.error_message,
            )
        else:
            if not _finalize_governed_pipeline_run(pipeline_run, status="Transforming"):
                # The run was finished while this job started: an EPM admin
                # cancelled it (orchestrator cancel_run), or the reaper failed
                # it. Another build may already hold the dbt project (#67 fix
                # 5), so don't start dbt; finish this job's own row (#140 re-review).
                frappe.logger().warning(
                    f"Governed build {doc.name}: its Pipeline Run {pipeline_run} was finished before dbt "
                    "started; not building"
                )
                _stop_before_dbt(doc, pipeline_run)
                return

            # Build dbt command
            settings = frappe.get_single("EPM Settings")
            project_path = settings.dbt_project_path
            cmd = dbt_build_command(_dbt_bin(), project_path, _scope_selector(doc.build_scope))

            # Execute
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=300,
                cwd=project_path,
            )
            output = result.stdout + "\n" + result.stderr
            doc.build_output = output[-5000:]  # cap at 5K chars

            if result.returncode == 0:
                doc.workflow_state = "Completed"
                _finalize_governed_pipeline_run(
                    pipeline_run,
                    status="Completed",
                    dbt_result=doc.build_output[:500],
                )
            else:
                doc.workflow_state = "Failed"
                doc.error_message = f"dbt build failed (rc={result.returncode})"
                _finalize_governed_pipeline_run(
                    pipeline_run,
                    status="Failed",
                    error_log=doc.error_message,
                )

    except subprocess.TimeoutExpired:
        doc.workflow_state = "Failed"
        doc.error_message = "dbt build timed out after 300s"
        _finalize_governed_pipeline_run(
            pipeline_run,
            status="Failed",
            error_log=doc.error_message,
        )
    except Exception as e:
        doc.workflow_state = "Failed"
        doc.error_message = str(e)
        _finalize_governed_pipeline_run(
            pipeline_run,
            status="Failed",
            error_log=doc.error_message,
        )

    _finish_governed_build(doc)


def _finish_governed_build(doc):
    """Persist a governed build's terminal state, then request the follow-up
    that a change made during the build asked for (#129).

    The flag is re-read under the row lock. A request that flags this row
    holds the same lock (its debounce's FOR UPDATE) until it commits, so
    either the flag is seen here, or the request finds this row already
    terminal and inserts its own approval. Nothing falls between.
    """
    from konsol.build_lock import build_writer

    flagged = frappe.db.sql(
        "SELECT rebuild_requested FROM `tabBuild Approval` WHERE name = %s FOR UPDATE", doc.name
    )[0][0]
    doc.rebuild_requested = flagged
    doc.completed_at = frappe.utils.now_datetime()
    _set_duration(doc)
    # The build path's own move out of Running (BuildApproval.before_save).
    with build_writer():
        doc.save(ignore_permissions=True)
    frappe.db.commit()

    frappe.publish_realtime(
        "build_request_complete",
        {
            "name": doc.name,
            "state": doc.workflow_state,
            "scope": doc.build_scope,
            "duration": doc.duration_seconds,
        },
    )

    if flagged:
        # After the commit: the request takes the build lock, and must not
        # wait for it while holding this row.
        try:
            request_build_for_scope(doc.build_scope, "Build Approval", doc.name, carries_changes=True)
        except Exception:
            frappe.db.rollback()   # the job commits on return; don't keep half a request
            frappe.log_error(title=f"Follow-up build request for {doc.name} failed")


STOPPED_BEFORE_DBT = "Governed build stopped: its Pipeline Run was finished before dbt started"


def _stop_before_dbt(doc, pipeline_run):
    """Finish this job's own Running row as Failed: its Pipeline Run was
    finished (cancelled, or reaped) before dbt started (#140 re-review).

    Re-read under the row lock. Only a row still Running from this job's
    start is this job's to finish; one the reaper failed meanwhile is left as
    it is. Through the finish, so a change absorbed while it ran is still
    followed up; without this the row stayed Running until the reaper, some
    45 minutes on.
    """
    row = frappe.db.sql(
        "SELECT workflow_state, started_at FROM `tabBuild Approval` WHERE name = %s FOR UPDATE",
        doc.name, as_dict=True,
    )
    if not row or row[0].workflow_state != "Running" or row[0].started_at != doc.started_at:
        frappe.db.rollback()
        return
    doc.workflow_state = "Failed"
    doc.error_message = f"{STOPPED_BEFORE_DBT} ({pipeline_run}: cancelled, or reaped)"
    _finish_governed_build(doc)


def _set_duration(doc):
    """Calculate duration_seconds from started_at to completed_at."""
    if doc.started_at and doc.completed_at:
        delta = doc.completed_at - doc.started_at
        doc.duration_seconds = round(delta.total_seconds(), 1)


# ---------------------------------------------------------------------------
# Hook: trigger governed build after consolidation/allocation doc changes
# ---------------------------------------------------------------------------
def queue_consolidation_build(doc, method):
    """doc_events target for the build-trigger doctypes, and Entity's
    targeted trigger: queue the build request as a job that runs after the
    commit (konsol#126). The one enqueue path.

    on_consolidation_doc_update inserts a Build Approval and COMMITS. Wired
    straight to the document hooks, that commit landed mid-transaction: on
    submit Frappe runs on_update BEFORE on_submit, so docstatus=1 was committed
    before the controller's on_submit synced to ClickHouse, and a failed sync
    left the document Submitted with nothing behind it. As a job it runs in
    its own transaction. A save that rolls back queues nothing; a failed
    request is rolled back and logged by the job runner.
    """
    if (frappe.flags.in_install or frappe.flags.in_migrate
            or frappe.flags.in_patch or frappe.flags.in_import):
        return
    # A submittable doctype reaches the warehouse only once submitted
    # (resolve_sync_filters: docstatus=1), so a draft save changes nothing
    # dbt reads; and on submit, on_update fires as well as on_submit. For
    # these, on_submit and on_cancel carry the signal.
    if method == "on_update" and doc.meta.is_submittable:
        return
    try:
        frappe.enqueue(
            "konsol.tasks.request_consolidation_build",
            enqueue_after_commit=True,
            job_id=f"konsol-build-request::{doc.doctype}::{doc.name}",
            deduplicate=True,
            doctype=doc.doctype,
            name=doc.name,
            # NOT `method=`: that is frappe.enqueue's own first parameter,
            # and passing it again raised TypeError on every save (#110).
            trigger_method=method,
        )
    except Exception:  # noqa: BLE001 — a build request must never fail the save
        # deduplicate=True makes enqueue query Redis now, inside the save.
        frappe.log_error(title=f"{doc.doctype} {doc.name}: build not requested")


def request_consolidation_build(doctype, name, trigger_method):
    """RQ job target, queued by queue_consolidation_build after the commit.

    Runs on_consolidation_doc_update in the job's own transaction, so its Build
    Approval insert and commit never land inside the caller's document hooks.
    The trigger reads only doctype and name, so a deleted document's request
    still resolves."""
    on_consolidation_doc_update(frappe._dict(doctype=doctype, name=name), trigger_method)


def on_consolidation_doc_update(doc, method):
    """The build request itself. It runs INSIDE request_consolidation_build's
    job, never from a document hook: it commits (konsol#126).

    Creates a Build Approval instead of firing raw dbt build.
    Uses DOCTYPE_BUILD_MAP to determine scope and risk level.
    """
    # Inert during app install / migrate / fixture import: loading fixtures
    # (e.g. allocation_rule.json) must not enqueue pipeline builds — and
    # ClickHouse credentials (EPM Settings) may not be configured yet, so a
    # build request here would crash the install on get_connection().
    if (
        frappe.flags.in_install
        or frappe.flags.in_migrate
        or frappe.flags.in_patch
        or frappe.flags.in_import
    ):
        return

    mapping = DOCTYPE_BUILD_MAP.get(doc.doctype)
    if not mapping:
        frappe.logger().warning(f"No build mapping for doctype: {doc.doctype}")
        return

    request_build_for_scope(mapping["scope"], doc.doctype, doc.name)


def request_build_for_scope(scope, trigger_doctype, trigger_docname, carries_changes=False):
    """Request a build of ``scope``: debounced, serialised, and committed.

    It commits, so it runs only inside a job: request_consolidation_build's
    (via on_consolidation_doc_update), a finished build's follow-up
    (_finish_governed_build), and the reaper's (konsol#126, #129, #140).

    ``carries_changes``: this is a follow-up, requested for changes an earlier
    build absorbed and never read. The new approval is flagged like an
    absorbing one, so if it too fails to start, the sweep retries it (#140
    review). Absorbed into a pending build instead, the debounce flags that.
    """
    # Serialise every build request (konsol.build_lock). The debounce below is
    # check-then-insert: two workers running this at once both found nothing
    # pending and both inserted a Build Approval (#110 re-review; per-entity
    # jobs on several workers made it likely). The lock is held until the
    # commit below.
    from konsol.build_lock import flag_running_build, lock_build_requests

    lock_build_requests()

    # Debounce: skip if a non-terminal PBR already exists for this scope
    # A LOCKING read. Under REPEATABLE READ a plain read reuses the snapshot from
    # the transaction's first read, taken before the build lock above: a
    # request that waited on the lock would not see the approval the holder had
    # just committed, and would insert a duplicate (#133 review). FOR UPDATE
    # reads the latest committed rows.
    existing = frappe.db.sql(
        """SELECT name, workflow_state FROM `tabBuild Approval`
           WHERE build_scope = %s
             AND workflow_state IN ('Draft', 'Pending Review', 'Approved', 'Running')
           LIMIT 1 FOR UPDATE""",
        scope,
        as_dict=True,
    )
    if existing:
        # A Running build may already have read its inputs, so this change
        # would miss gold: flag it, and it requests one more build when it
        # finishes (#129). A pending one will read it anyway.
        flag_running_build(existing[0])
        frappe.db.commit()
        frappe.logger().info(
            f"Build request already pending for scope={scope} ({existing[0].name}), skipping"
        )
        return

    # Create Build Approval
    pbr = frappe.new_doc("Build Approval")
    pbr.build_scope = scope
    pbr.trigger_source = "auto"
    pbr.trigger_doctype = trigger_doctype
    pbr.trigger_docname = trigger_docname
    pbr.requested_by = frappe.session.user
    pbr.rebuild_requested = 1 if carries_changes else 0
    pbr.insert(ignore_permissions=True)
    frappe.db.commit()

    frappe.logger().info(
        f"Build Approval {pbr.name} created (scope={scope}, trigger={trigger_doctype} {trigger_docname})"
    )


# ---------------------------------------------------------------------------
# Legacy: Lightweight dbt-only build (kept for backward compat)
# ---------------------------------------------------------------------------
def run_dbt_build_async(doctype=None, docname=None):
    """Run dbt build as a background job. Debounced: skips if one is already queued.

    DEPRECATED: Use on_consolidation_doc_update → Build Approval instead.
    """
    from frappe.utils.background_jobs import get_jobs
    site = frappe.local.site
    queued_jobs = get_jobs(site=site, queue="default")
    for job_list in queued_jobs.values():
        if "konsol.tasks._run_dbt_build_background" in job_list:
            frappe.logger().info("dbt build already queued, skipping duplicate")
            return

    frappe.enqueue(
        "konsol.tasks._run_dbt_build_background",
        queue="default",
        timeout=600,
        doctype=doctype,
        docname=docname,
    )
    frappe.logger().info(
        f"dbt build enqueued (triggered by {doctype} {docname})"
    )


def _run_dbt_build_background(doctype=None, docname=None, pipeline_run=None):
    """Background worker: execute dbt build and log result.

    If pipeline_run is provided, updates its status on completion/failure.
    """
    settings = frappe.get_single("EPM Settings")
    project_path = settings.dbt_project_path

    try:
        result = subprocess.run(
            [_dbt_bin(), "build", "--project-dir", project_path, "--profiles-dir", project_path],
            capture_output=True,
            text=True,
            timeout=300,
            cwd=project_path,
        )
        output = result.stdout + "\n" + result.stderr

        if result.returncode == 0:
            summary = ""
            for line in output.split("\n"):
                if "pass" in line.lower() and ("warn" in line.lower() or "error" in line.lower()):
                    summary = line.strip()
                    break
            frappe.logger().info(f"dbt build completed: {summary or 'success'}")
            frappe.publish_realtime(
                "dbt_build_complete",
                {"status": "success", "summary": summary, "trigger": f"{doctype} {docname}"},
            )
            _update_pipeline_run(pipeline_run, "Completed", dbt_result=summary,
                                 log=output, project_path=project_path)
        else:
            frappe.logger().error(f"dbt build failed (rc={result.returncode}):\n{output[-2000:]}")
            frappe.publish_realtime(
                "dbt_build_complete",
                {"status": "failed", "error": output[-500:]},
            )
            _update_pipeline_run(pipeline_run, "Failed", error_log=output[-2000:],
                                 log=output, project_path=project_path)
    except subprocess.TimeoutExpired:
        frappe.logger().error("dbt build timed out after 300s")
        _update_pipeline_run(pipeline_run, "Failed", error_log="dbt build timed out after 300s")
    except Exception as e:
        frappe.logger().error(f"dbt build error: {e}")
        _update_pipeline_run(pipeline_run, "Failed", error_log=str(e))


def _update_pipeline_run(pipeline_run, status, dbt_result=None, error_log=None,
                         log=None, project_path=None):
    """Update a Pipeline Run's status if name was provided.

    When project_path is given, also parses target/run_results.json into the
    `steps` child table (Press-style per-node state) and stores the full `log`.
    """
    if not pipeline_run:
        return
    try:
        doc = frappe.get_doc("Pipeline Run", pipeline_run)
        doc.status = status
        doc.completed_at = frappe.utils.now_datetime()
        if dbt_result:
            doc.dbt_result = dbt_result
        if error_log:
            doc.error_log = error_log
        if log is not None:
            doc.log = log[-20000:]
        if project_path:
            _populate_run_steps(doc, project_path)
        doc.save(ignore_permissions=True)
        frappe.db.commit()
        frappe.publish_realtime(
            "pipeline_run_update",
            {"run": doc.name, "done": True, "progress": doc.progress_pct},
            doctype="Pipeline Run", docname=doc.name,
        )
    except Exception:
        frappe.log_error("Failed to update Pipeline Run status", frappe.get_traceback())


def _populate_run_steps(doc, project_path):
    """Read dbt target/run_results.json into the Pipeline Run `steps` table."""
    import json
    import os

    rr_path = os.path.join(project_path, "target", "run_results.json")
    try:
        with open(rr_path) as fh:
            rr = json.load(fh)
    except Exception:
        return

    # Run Step.status vocabulary is unified on "Failed" (see #65c-i) — the
    # orchestrator (run.py) already writes "Failed", and "Failure" was dropped
    # from the doctype options, so the legacy build path maps dbt errors to it too.
    status_map = {"success": "Success", "error": "Failed", "fail": "Failed",
                  "pass": "Success", "skipped": "Skipped"}
    doc.set("steps", [])
    done = 0
    nodes = rr.get("results", [])
    for node in nodes:
        uid = node.get("unique_id", "")
        parts = uid.split(".")
        rtype = parts[0] if parts else ""
        name = parts[2] if len(parts) > 2 else uid
        rel = (node.get("relation_name") or "").lower()
        if rtype == "seed":
            stage = "Seed"
        elif rtype == "test":
            stage = "Test"
        elif "bronze" in rel:
            stage = "Bronze"
        elif "silver" in rel:
            stage = "Silver"
        elif "gold" in rel:
            stage = "Gold"
        elif "staging" in rel or "stg_" in name:
            stage = "Staging"
        else:
            stage = "Model"
        st = status_map.get((node.get("status") or "").lower(), "Pending")
        if st in ("Success", "Skipped", "Failed"):
            done += 1
        rows = (node.get("adapter_response") or {}).get("rows_affected") or 0
        doc.append("steps", {
            "stage": stage,
            "step": name,
            "status": st,
            "rows": rows,
            "duration": round(node.get("execution_time") or 0, 2),
            "output": (node.get("message") or "")[:2000],
        })
    doc.progress_pct = int(100 * done / len(nodes)) if nodes else 0


# ---------------------------------------------------------------------------
# Full pipeline: Airbyte extract + dbt build
# ---------------------------------------------------------------------------
def run_pipeline(pipeline_run):
    """Main entry point — called by frappe.enqueue from trigger_pipeline."""
    doc = frappe.get_doc("Pipeline Run", pipeline_run)

    try:
        # Step 1: Airbyte extract (skipped when building from a pre-loaded
        # epm_raw — demo data or a manual load — so a missing/failing Airbyte
        # connector doesn't gate the dbt build).
        if frappe.get_single("EPM Settings").get("skip_airbyte_sync"):
            _update_status(doc, "Extracting")
            doc.airbyte_job_id = "skipped"
            doc.rows_synced = 0
        else:
            _update_status(doc, "Extracting")
            job_id, rows = _run_airbyte_sync(doc)
            doc.airbyte_job_id = job_id
            doc.rows_synced = rows

        # Step 2: dbt build
        _update_status(doc, "Transforming")
        dbt_result = _run_dbt_build(doc)
        doc.dbt_result = dbt_result

        # Done
        doc.status = "Completed"
        doc.completed_at = frappe.utils.now_datetime()
        doc.save(ignore_permissions=True)
        frappe.db.commit()

        frappe.publish_realtime(
            "pipeline_progress",
            {"name": doc.name, "status": "Completed", "dbt_result": dbt_result},
            doctype="Pipeline Run",
            docname=doc.name,
        )

    except Exception as e:
        doc.status = "Failed"
        doc.error_log = str(e)
        doc.completed_at = frappe.utils.now_datetime()
        doc.save(ignore_permissions=True)
        frappe.db.commit()

        frappe.publish_realtime(
            "pipeline_progress",
            {"name": doc.name, "status": "Failed", "error": str(e)},
            doctype="Pipeline Run",
            docname=doc.name,
        )


def _update_status(doc, status):
    """Update doc status and publish realtime event."""
    doc.status = status
    doc.save(ignore_permissions=True)
    frappe.db.commit()

    frappe.publish_realtime(
        "pipeline_progress",
        {"name": doc.name, "status": status},
        doctype="Pipeline Run",
        docname=doc.name,
    )


def _run_airbyte_sync(doc):
    """Authenticate to Airbyte API, trigger sync, poll until done.

    Uses AirbyteClient so auth and the API base path stay in one place — the
    application token only works against the public API (/api/public/v1).
    """
    settings = frappe.get_single("EPM Settings")

    client = AirbyteClient(
        settings.airbyte_api_url,
        settings.airbyte_client_id,
        settings.get_password("airbyte_client_secret"),
    )
    connection_id = settings.airbyte_connection_id

    # Trigger sync job
    job = client.request(
        "POST",
        "/jobs",
        json_body={"connectionId": connection_id, "jobType": "sync"},
    )
    job_id = str(job["jobId"])

    # Poll until complete
    job_status = ""
    for _ in range(120):  # max 60 minutes (30s intervals)
        time.sleep(30)
        job_data = client.request("GET", f"/jobs/{job_id}")
        job_status = job_data.get("status", "")

        if job_status == "succeeded":
            return job_id, job_data.get("rowsSynced", 0)
        elif job_status in ("failed", "cancelled"):
            raise Exception(f"Airbyte sync {job_status}: {job_data}")

    # Loop exhausted without a terminal status — don't report success with 0 rows.
    raise Exception(
        f"Airbyte sync did not finish within 60 minutes "
        f"(job {job_id}, last status '{job_status or 'unknown'}')."
    )


def _run_dbt_build(doc):
    """Run dbt build via subprocess, return summary string."""
    settings = frappe.get_single("EPM Settings")
    project_path = settings.dbt_project_path

    result = subprocess.run(
        [_dbt_bin(), "build", "--project-dir", project_path, "--profiles-dir", project_path],
        capture_output=True,
        text=True,
        timeout=300,
        cwd=project_path,
    )

    output = result.stdout + "\n" + result.stderr

    if result.returncode != 0:
        raise Exception(f"dbt build failed (rc={result.returncode}):\n{output[-2000:]}")

    # Parse summary line like "Completed successfully. 42 pass, 0 warn, 0 error"
    summary = ""
    for line in output.split("\n"):
        if "pass" in line.lower() and ("warn" in line.lower() or "error" in line.lower()):
            summary = line.strip()
            break

    return summary or output[-500:]

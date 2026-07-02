"""Konsol Control — operator dashboard API.

Aggregates readiness checks, run status, budget layers, and history for the
three close processes: Budgeting, Forecasting, and Consolidation.
"""
from __future__ import annotations

import frappe
from frappe.utils import add_to_date, get_datetime, now_datetime, today

from konsol.epm.doctype.budget_sheet.budget_sheet import LAYER_ROLES
from konsol.schema_lifecycle import check_epm_admin

PROCESSES = {
    "budgeting": {
        "name": "Budgeting",
        "num": "01",
        "accent": "#b5611f",
        "desc": "Layered budget submission → board lock → ClickHouse publish.",
        "build_scope": "scenarios",
    },
    "forecasting": {
        "name": "Forecasting",
        "num": "02",
        "accent": "#0e8f84",
        "desc": "Refresh actuals, run allocations, publish forecast scenarios.",
        "build_scope": "actuals",
    },
    "consolidation": {
        "name": "Consolidation",
        "num": "03",
        "accent": "#2f7d4f",
        "desc": "Run the group consolidation build — extract → seed → silver → gold.",
        "build_scope": "consolidation",
    },
    "assertions": {
        "name": "Assertions",
        "num": "04",
        "accent": "#0e8f84",
        "desc": "Run the close assertion suite (dbt tests) and sign-off.",
        "build_scope": "consolidation",
    },
}

LAYER_META = [
    ("base", "Base submission", "Week 1–2"),
    ("challenge", "Finance challenge", "Week 2–3"),
    ("management", "Management override", "Week 3"),
    ("board", "Board adjustment", "Week 4"),
]

_RUNNING_PIPELINE = ("Queued", "Extracting", "Transforming")
_RUNNING_CLOSE = ("Queued", "Running")
_RUNNING_PBR = ("Running", "Approved")


def _count(doctype, filters=None):
    return frappe.db.count(doctype, filters or {})


def _exists(doctype, filters=None):
    if filters is None and frappe.get_meta(doctype).issingle:
        return bool(frappe.db.exists(doctype))
    return bool(frappe.db.exists(doctype, filters or {}))


def _current_fiscal_year():
    return frappe.utils.getdate(today()).year


@frappe.whitelist(methods=["GET", "POST"])
def get_snapshot():
    """Full control-plane state for the Konsol Exec SPA."""
    fy = _current_fiscal_year()
    budget_locked = _budget_is_locked(fy)
    processes = {}
    for pid, meta in PROCESSES.items():
        prereqs = _prerequisites(pid, fy, budget_locked)
        ready = sum(1 for p in prereqs if p["status"] == "configured")
        blockers = sum(1 for p in prereqs if p["status"] in ("missing", "blocked", "stale"))
        run = _active_run(pid)
        processes[pid] = {
            **meta,
            "id": pid,
            "prerequisites": prereqs,
            "ready_count": ready,
            "total_count": len(prereqs),
            "blockers": blockers,
            "runnable": blockers == 0 and (pid != "forecasting" or budget_locked),
            "run": run,
            "machine_status": _machine_status(run),
        }

    stats = _stats(processes)
    return {
        "fiscal_year": fy,
        "worker_healthy": _worker_healthy(),
        "budget_locked": budget_locked,
        "processes": processes,
        "stats": stats,
        "budget_rounds": _budget_rounds(fy),
        "reminders": _reminders(processes, fy),
        "runs": _domain_runs_all(),
        "scenarios": _scenario_options(),
        "periods": _period_options(fy),
    }


@frappe.whitelist()
def start_process(process_id, fiscal_year=None, fiscal_period=None):
    """Kick off the run for a close process."""
    check_epm_admin()
    pid = (process_id or "").strip()
    if pid not in PROCESSES:
        frappe.throw(f"Unknown process: {process_id}")

    fy = int(fiscal_year or _current_fiscal_year())
    fp = int(fiscal_period) if fiscal_period else None

    if pid == "forecasting":
        from konsol.pipeline.doctype.pipeline_run.pipeline_run import trigger_pipeline
        name = trigger_pipeline()
        return {"ok": True, "run_kind": "pipeline", "name": name}

    if pid == "consolidation":
        # the consolidation BUILD = an orchestrator run (Group Close pipeline)
        from konsol.orchestrator.api import start_run
        params = {"fiscal_year": fy} if fy else {}
        if fp:
            params["fiscal_period"] = fp
        name = start_run(definition="Group Close", params=params)
        return {"ok": True, "run_kind": "pipeline", "name": name}

    if pid == "assertions":
        from konsol.consolidation.doctype.period_close.period_close import trigger_close_run
        name = trigger_close_run(fiscal_year=fy, fiscal_period=fp)
        return {"ok": True, "run_kind": "close", "name": name}

    # budgeting — governed scenarios build
    scope = PROCESSES[pid]["build_scope"]
    pbr = frappe.get_doc({
        "doctype": "Build Approval",
        "build_scope": scope,
        "trigger_source": "manual",
        "requested_by": frappe.session.user,
        "workflow_state": "Draft",
    })
    pbr.insert(ignore_permissions=True)
    if pbr.workflow_state == "Pending Review" and "System Manager" in frappe.get_roles():
        pbr.workflow_state = "Approved"
        pbr.approved_by = frappe.session.user
        pbr.save(ignore_permissions=True)
    frappe.db.commit()
    return {"ok": True, "run_kind": "pbr", "name": pbr.name}


@frappe.whitelist(methods=["GET", "POST"])
def get_run_detail(process_id, kind, run_id):
    """Drill-down for a single domain run — steps, logs, and related doctypes."""
    pid = (process_id or "").strip()
    if pid not in PROCESSES:
        frappe.throw(f"Unknown process: {process_id}")

    run_kind = (kind or "").strip().lower()
    name = (run_id or "").strip()
    if not name:
        frappe.throw("run_id is required")

    if pid == "budgeting":
        if run_kind != "pbr":
            frappe.throw("Budget runs use kind=pbr")
        return _pbr_run_detail(name, pid)

    if pid == "forecasting":
        if run_kind != "pipeline":
            frappe.throw("Forecast runs use kind=pipeline")
        return _pipeline_run_detail(name, pid)

    # Consolidation has two run types: orchestrator builds (Pipeline Run) and
    # assertion runs (Period Close).
    if run_kind == "pipeline":
        return _orchestrator_run_detail(name, pid)
    if run_kind == "close":
        return _close_run_detail(name, pid)
    frappe.throw("Consolidation runs use kind=pipeline or close")


@frappe.whitelist()
def send_reminder(owner, item=""):
    """Log a reminder (email integration can be added later)."""
    frappe.logger().info("Konsol Control reminder: %s — %s", owner, item)
    return {"ok": True, "message": f"Reminder logged for {owner}"}


@frappe.whitelist()
def doctype_route(doctype):
    """Return the Frappe desk List route target for a doctype."""
    return {"doctype": doctype, "view": "List"}


def _worker_healthy():
    try:
        from konsol.api import health
        return health().get("status") in ("ok", "degraded")
    except Exception:
        return False


def _budget_is_locked(fy):
    return _exists("Budget Cycle", {"fiscal_year": fy, "status": "Locked", "docstatus": 1})


def _prerequisites(process_id, fy, budget_locked):
    """Return prerequisite rows for Setup & readiness."""
    common = [
        _check("EPM Settings", "Setup → EPM Settings", _epm_settings_ok, owner="EPM Admin"),
        _check("Fiscal Period", "Lists → EPM → Fiscal Period", lambda: _count("Fiscal Period") >= 12, owner="EPM Admin"),
        _check("Dimension", "Lists → EPM → Dimension", lambda: _count("Dimension", {"in_budget": 1}) >= 1, owner="EPM Admin"),
        _check("Measure", "Lists → EPM → Measure", lambda: _count("Measure") >= 1, owner="EPM Admin"),
        _check(
            "Scenario Definition",
            "Lists → EPM → Scenario Definition",
            lambda: _exists("Scenario Definition", {"is_active": 1, "scenario_type": ["in", ("budget", "forecast", "actual")]}),
            owner="EPM Admin",
        ),
    ]

    if process_id == "budgeting":
        return common + [
            _check("Spread Profile", "Lists → EPM → Spread Profile", lambda: _exists("Spread Profile"), owner="EPM Admin"),
            _check(
                "Budget Cycle",
                "Lists → EPM → Budget Cycle",
                lambda: _exists("Budget Cycle", {"fiscal_year": fy}),
                owner="Budget Manager",
            ),
            _check(
                "Budget Sheet",
                "Lists → EPM → Budget Sheet",
                lambda: _count("Budget Sheet", {"layer": "base"}) >= 1,
                owner="Budget Submitter",
            ),
        ]

    if process_id == "forecasting":
        deps = common + [
            _check(
                "Pipeline Run",
                "Lists → Pipeline → Pipeline Run",
                _recent_pipeline_ok,
                owner="Data Engineer",
                stale_hours=24,
            ),
            _check(
                "Allocation Driver",
                "Lists → Allocation → Allocation Driver",
                lambda: _count("Allocation Driver") >= 1,
                owner="EPM Analyst",
            ),
        ]
        if not budget_locked:
            deps.append({
                "doctype": "Budget Cycle",
                "location": "Lists → EPM → Budget Cycle",
                "owner": "Budget Approver",
                "due": "Before forecast",
                "status": "blocked",
                "status_label": "Blocked",
                "actionable": True,

            })
        return deps

    # consolidation
    return common + [
        _check(
            "Consolidation Group",
            "Lists → Consolidation → Consolidation Group",
            lambda: _exists("Consolidation Group", {"consolidation_group": "GROUP_CORP"}),
            owner="EPM Admin",
        ),
        _check(
            "IC Elimination Rule",
            "Lists → Consolidation → IC Elimination Rule",
            lambda: _count("IC Elimination Rule") >= 1,
            owner="EPM Admin",
        ),
        _check(
            "Ownership Period",
            "Lists → Consolidation → Ownership Period",
            lambda: _count("Ownership Period") >= 1,
            owner="EPM Admin",
        ),
        _check(
            "Historical Equity Rate",
            "Lists → Consolidation → Historical Equity Rate",
            lambda: _count("Historical Equity Rate") >= 1,
            owner="EPM Admin",
            note="FX rates flow from ERP via pipeline; equity overrides here.",
        ),
    ]


def _check(doctype, location, predicate, owner="EPM Admin", stale_hours=None, note=None):
    try:
        ok = bool(predicate())
    except Exception:
        ok = False
    status = "configured" if ok else "missing"
    if ok and stale_hours and doctype == "Pipeline Run":
        if not _recent_pipeline_ok(stale_hours):
            status = "stale"
    return {
        "doctype": doctype,
        "location": location,
        "owner": owner,
        "due": note or "",
        "status": status,
        "status_label": {"configured": "Configured", "missing": "Missing", "stale": "Stale"}.get(status, status),
        "actionable": status in ("missing", "stale", "blocked"),

    }


def _epm_settings_ok():
    if not _exists("EPM Settings"):
        return False
    settings = frappe.get_single("EPM Settings")
    return bool(settings.clickhouse_host)


def _recent_pipeline_ok(stale_hours=24):
    row = frappe.get_all(
        "Pipeline Run",
        filters={"status": "Completed"},
        fields=["completed_at"],
        order_by="completed_at desc",
        limit=1,
    )
    if not row or not row[0].completed_at:
        return False
    cutoff = add_to_date(now_datetime(), hours=-stale_hours)
    return get_datetime(row[0].completed_at) >= cutoff


def _active_run(process_id):
    if process_id == "consolidation":
        return _latest_orchestrator_run()
    if process_id == "assertions":
        return _latest_close_run()
    if process_id == "forecasting":
        return _latest_pipeline_run(scope=None)
    return _latest_pbr_run(PROCESSES[process_id]["build_scope"])


def _latest_orchestrator_run():
    """Latest orchestrator (Execute-plane) build run — the most recent Pipeline
    Run that has typed steps (step_id set), distinguishing it from legacy
    forecast/budget pipeline runs."""
    for r in frappe.get_all("Pipeline Run", fields=["name"], order_by="creation desc", limit=25):
        if frappe.db.exists("Run Step", {"parent": r.name, "step_id": ["is", "set"]}):
            return _serialize_pipeline_run(r.name)
    return None


def _latest_pipeline_run(scope=None):
    filters = {}
    rows = frappe.get_all(
        "Pipeline Run",
        filters=filters,
        fields=[
            "name", "status", "progress_pct", "started_at", "completed_at",
            "rows_synced", "triggered_by", "error_log", "log", "pipeline_build_request",
        ],
        order_by="creation desc",
        limit=1,
    )
    if not rows:
        return None
    row = rows[0]
    if scope and row.pipeline_build_request:
        pbr_scope = frappe.db.get_value("Build Approval", row.pipeline_build_request, "build_scope")
        if pbr_scope != scope:
            return None
    if not scope and row.pipeline_build_request:
        pbr_scope = frappe.db.get_value("Build Approval", row.pipeline_build_request, "build_scope")
        if pbr_scope in ("scenarios", "consolidation"):
            return None
    return _serialize_pipeline_run(row.name)


def _latest_pbr_run(scope):
    pbr = frappe.get_all(
        "Build Approval",
        filters={"build_scope": scope},
        fields=["name", "workflow_state", "started_at", "completed_at", "duration_seconds", "error_message"],
        order_by="creation desc",
        limit=1,
    )
    if not pbr:
        return None
    row = pbr[0]
    pipe = frappe.get_all(
        "Pipeline Run",
        filters={"pipeline_build_request": row.name},
        fields=["name"],
        order_by="creation desc",
        limit=1,
    )
    if pipe:
        return _serialize_pipeline_run(pipe[0].name)
    return {
        "kind": "pbr",
        "name": row.name,
        "status": row.workflow_state,
        "machine_status": _pbr_machine(row.workflow_state),
        "started_at": row.started_at,
        "completed_at": row.completed_at,
        "elapsed_ms": int((row.duration_seconds or 0) * 1000),
        "steps": [],
        "logs": _logs_from_text(row.error_message),
        "rows": "—",
    }


def _latest_close_run():
    rows = frappe.get_all(
        "Period Close",
        fields=["name", "status", "fiscal_year", "fiscal_period", "started_at", "completed_at",
                "duration_seconds", "total", "passed", "failed", "triggered_by", "log"],
        order_by="creation desc",
        limit=1,
    )
    if not rows:
        return None
    row = rows[0]
    doc = frappe.get_doc("Period Close", row.name)
    steps = []
    for i, res in enumerate(doc.results or [], start=1):
        st = "done" if res.status == "Pass" else ("error" if res.status in ("Fail", "Error") else "pending")
        steps.append({
            "num": f"{i:02d}",
            "name": res.assertion or res.name,
            "detail": res.dimension or "",
            "rows": str(res.rows_failed or ""),
            "state": st,
            "pct": 100 if st == "done" else (0 if st == "pending" else 100),
            "duration_ms": 0,
        })
    done = sum(1 for s in steps if s["state"] == "done")
    return {
        "kind": "close",
        "name": row.name,
        "status": row.status,
        "machine_status": _close_machine(row.status),
        "started_at": row.started_at,
        "completed_at": row.completed_at,
        "elapsed_ms": int((row.duration_seconds or 0) * 1000),
        "steps": steps,
        "logs": _logs_from_text(row.log),
        "rows": str(row.total or 0),
        "step_done": done,
        "step_total": len(steps) or row.total or 0,
        "period": f"FY{row.fiscal_year} · P{row.fiscal_period}" if row.fiscal_period else f"FY{row.fiscal_year}",
    }


def _serialize_pipeline_run(name):
    doc = frappe.get_doc("Pipeline Run", name)
    steps = []
    for i, step in enumerate(doc.steps or [], start=1):
        st = _step_state(step.status)
        steps.append({
            "num": f"{i:02d}",
            "name": step.step or step.stage,
            "detail": step.stage or "",
            "rows": str(step.rows or ""),
            "state": st,
            "pct": 100 if st == "done" else (50 if st == "running" else 0),
            "duration_ms": int((step.duration or 0) * 1000),
            "error": step.output if st == "error" else None,
        })
    if not steps and doc.status in _RUNNING_PIPELINE + ("Completed", "Failed"):
        steps = [
            {"num": "01", "name": "Airbyte extract", "detail": "Extract", "rows": str(doc.rows_synced or ""), "state": _pipe_phase(doc.status, "Extracting"), "pct": 100 if doc.status != "Queued" else 0, "duration_ms": 0},
            {"num": "02", "name": "dbt transform", "detail": "Transform", "rows": "", "state": _pipe_phase(doc.status, "Transforming"), "pct": doc.progress_pct or 0, "duration_ms": 0},
        ]
    done = sum(1 for s in steps if s["state"] == "done")
    elapsed = 0
    if doc.started_at:
        end = doc.completed_at or now_datetime()
        elapsed = int((get_datetime(end) - get_datetime(doc.started_at)).total_seconds() * 1000)
    return {
        "kind": "pipeline",
        "name": doc.name,
        "status": doc.status,
        "machine_status": _pipeline_machine(doc.status),
        "started_at": doc.started_at,
        "completed_at": doc.completed_at,
        "elapsed_ms": elapsed,
        "steps": steps,
        "logs": _logs_from_text(doc.log or doc.error_log),
        "rows": str(doc.rows_synced or ""),
        "step_done": done,
        "step_total": len(steps) or 2,
    }


def _step_state(status):
    return {
        "Success": "done",
        "Failed": "error",
        "Running": "running",
        "Pending": "pending",
        "Skipped": "done",
        "Cancelled": "error",
    }.get(status, "pending")


def _pipe_phase(doc_status, phase):
    order = ["Queued", "Extracting", "Transforming", "Completed"]
    if doc_status == "Failed":
        return "error"
    if doc_status == "Completed":
        return "done"
    if doc_status == phase:
        return "running"
    if order.index(doc_status) > order.index(phase):
        return "done"
    return "pending"


def _pipeline_machine(status):
    if status in _RUNNING_PIPELINE:
        return "running"
    if status == "Completed":
        return "done"
    if status == "Failed":
        return "error"
    return "idle"


def _close_machine(status):
    if status in _RUNNING_CLOSE:
        return "running"
    if status == "Green":
        return "done"
    if status in ("Red", "Error"):
        return "error"
    return "idle"


def _pbr_machine(state):
    if state in ("Running",):
        return "running"
    if state == "Completed":
        return "done"
    if state in ("Failed", "Cancelled"):
        return "error"
    if state == "Pending Review":
        return "paused"
    return "idle"


def _machine_status(run):
    if not run:
        return "idle"
    return run.get("machine_status") or "idle"


def _stats(processes):
    active = sum(1 for p in processes.values() if p["machine_status"] in ("running", "paused"))
    errors = sum(1 for p in processes.values() if p["machine_status"] == "error")
    done = sum(1 for p in processes.values() if p["machine_status"] == "done")
    return {"active": active, "errors": errors, "done_today": _completed_today()}


def _completed_today():
    start = frappe.utils.getdate(today())
    n = frappe.db.count("Pipeline Run", {"status": "Completed", "completed_at": [">=", start]})
    n += frappe.db.count("Period Close", {"status": "Green", "completed_at": [">=", start]})
    return n


def _budget_rounds(fy):
    cycle = frappe.db.get_value("Budget Cycle", {"fiscal_year": fy}, "name")
    cycle_locked = False
    if cycle:
        cycle_locked = frappe.db.get_value("Budget Cycle", cycle, "status") == "Locked"
    rounds = []
    for layer, title, week in LAYER_META:
        sheets = frappe.get_all(
            "Budget Sheet",
            filters={"layer": layer, **({"cycle": cycle} if cycle else {})},
            fields=["name", "annual_total", "data_area_id"],
        )
        total = sum(s.annual_total or 0 for s in sheets)
        if cycle_locked and layer == "board":
            state = "approved"
        elif sheets and total:
            state = "submitted" if layer != "base" else "draft"
        elif sheets:
            state = "draft"
        else:
            state = "pending"
        rounds.append({
            "key": layer,
            "layer": title,
            "role": LAYER_ROLES.get(layer, ""),
            "owner": LAYER_ROLES.get(layer, ""),
            "week": week,
            "amount": f"{total:+,}" if total else "—",
            "state": state,
            "sheet_count": len(sheets),

        })
    return {"cycle": cycle, "locked": cycle_locked, "rounds": rounds}


def _reminders(processes, fy):
    items = []
    for pid, proc in processes.items():
        for prereq in proc["prerequisites"]:
            if prereq["status"] in ("missing", "stale", "blocked"):
                items.append({
                    "process": proc["name"],
                    "process_id": pid,
                    "what": prereq["doctype"],
                    "owner": prereq["owner"],
                    "due": prereq.get("due") or "",
                    "severity": "overdue" if prereq["status"] == "blocked" else ("warn" if prereq["status"] == "stale" else "open"),
                })
    for rnd in _budget_rounds(fy)["rounds"]:
        if rnd["state"] not in ("approved",):
            items.append({
                "process": "Budgeting",
                "process_id": "budgeting",
                "what": rnd["layer"],
                "owner": rnd["owner"],
                "due": rnd["week"],
                "severity": "warn" if rnd["state"] != "pending" else "open",
            })
    return items


_RUN_LIST_LIMIT = 100


def _domain_runs_all(limit=_RUN_LIST_LIMIT):
    return {
        "budgeting": _budget_run_list(limit),
        "forecasting": _forecast_run_list(limit),
        # Consolidation = orchestrator BUILD runs (Pipeline Run); Assertions =
        # the close assertion runs (Period Close). Two top-level cards.
        "consolidation": _consolidation_build_list(limit),
        "assertions": _consolidation_run_list(limit),
    }


def _budget_run_list(limit):
    rows = []
    for pbr in frappe.get_all(
        "Build Approval",
        filters={"build_scope": PROCESSES["budgeting"]["build_scope"]},
        fields=[
            "name", "workflow_state", "build_scope", "requested_by", "approved_by",
            "started_at", "completed_at", "duration_seconds", "trigger_doctype", "trigger_docname",
            "creation",
        ],
        order_by="creation desc",
        limit=limit,
    ):
        rows.append(_pbr_list_row(pbr, "budgeting"))
    return rows


def _forecast_run_list(limit):
    rows = []
    for pr in frappe.get_all(
        "Pipeline Run",
        fields=[
            "name", "status", "started_at", "completed_at", "rows_synced",
            "triggered_by", "pipeline_build_request", "creation",
        ],
        order_by="creation desc",
        limit=limit * 3,
    ):
        if not _pipeline_belongs_to_forecast(pr.pipeline_build_request):
            continue
        rows.append(_pipeline_list_row(pr, "forecasting"))
        if len(rows) >= limit:
            break
    return rows


def _consolidation_run_list(limit):
    rows = []
    for cr in frappe.get_all(
        "Period Close",
        fields=[
            "name", "status", "title", "fiscal_year", "fiscal_period", "pipeline_run",
            "started_at", "completed_at", "duration_seconds", "total", "triggered_by", "creation",
        ],
        order_by="creation desc",
        limit=limit,
    ):
        rows.append(_close_list_row(cr))
    return rows


def _consolidation_build_list(limit):
    """Orchestrator (Execute-plane) build runs for the consolidation domain.

    These are Pipeline Runs created by the orchestrator — identified by having
    typed steps (a child row with ``step_id`` set), which the legacy forecast/
    budget Pipeline Runs do not. Tagged ``run_type="build"`` so the SPA History
    renders them in their own card, separate from the Period Close assertions.
    """
    rows = []
    for pr in frappe.get_all(
        "Pipeline Run",
        fields=[
            "name", "status", "started_at", "completed_at", "rows_synced",
            "triggered_by", "pipeline_build_request", "fiscal_year", "fiscal_period",
            "creation",
        ],
        order_by="creation desc",
        limit=limit * 3,
    ):
        if not frappe.db.exists("Run Step", {"parent": pr.name, "step_id": ["is", "set"]}):
            continue
        rows.append(_consolidation_build_row(pr))
        if len(rows) >= limit:
            break
    return rows


def _consolidation_build_row(pr):
    meta = PROCESSES["consolidation"]
    started = pr.started_at or pr.creation
    machine = _pipeline_machine(pr.status)
    if pr.fiscal_period:
        period = f"FY{pr.fiscal_year} · P{pr.fiscal_period}"
    elif pr.fiscal_year:
        period = f"FY{pr.fiscal_year}"
    else:
        period = "All periods"
    return {
        "id": pr.name,
        "kind": "pipeline",
        "run_type": "build",
        "process_id": "consolidation",
        "process": meta["name"],
        "accent": meta["accent"],
        "status": machine,
        "status_raw": pr.status,
        "period": period,
        "title": pr.name,
        "started": _fmt_dt(started),
        "completed": _fmt_dt(pr.completed_at),
        "duration": _fmt_duration(pr.started_at, pr.completed_at),
        "rows": str(pr.rows_synced or "—"),
        "by": pr.triggered_by or "—",
        "related_docs": _related_docs_pipeline(pr.name, pr.pipeline_build_request),
        "_sort": str(started or ""),
    }


def _pipeline_belongs_to_forecast(pbr_name):
    if not pbr_name:
        return True
    scope = frappe.db.get_value("Build Approval", pbr_name, "build_scope")
    return scope not in (PROCESSES["budgeting"]["build_scope"], PROCESSES["consolidation"]["build_scope"])


def _pbr_list_row(pbr, process_id):
    meta = PROCESSES[process_id]
    started = pbr.started_at or pbr.creation
    machine = _pbr_machine(pbr.workflow_state)
    pipes = frappe.get_all(
        "Pipeline Run",
        filters={"pipeline_build_request": pbr.name},
        fields=["name"],
        order_by="creation desc",
        limit=5,
    )
    related = _related_docs_pbr(pbr, pipes)
    return {
        "id": pbr.name,
        "kind": "pbr",
        "process_id": process_id,
        "process": meta["name"],
        "accent": meta["accent"],
        "status": machine,
        "status_raw": pbr.workflow_state,
        "period": f"Scope · {pbr.build_scope}",
        "started": _fmt_dt(started),
        "completed": _fmt_dt(pbr.completed_at),
        "duration": _fmt_duration(pbr.started_at, pbr.completed_at, pbr.duration_seconds),
        "rows": str(len(pipes)) + " pipeline run(s)" if pipes else "—",
        "by": pbr.requested_by or "—",
        "related_docs": related,
        "_sort": str(started or ""),
    }


def _pipeline_list_row(pr, process_id):
    meta = PROCESSES[process_id]
    started = pr.started_at or pr.creation
    machine = _pipeline_machine(pr.status)
    related = _related_docs_pipeline(pr.name, pr.pipeline_build_request)
    return {
        "id": pr.name,
        "kind": "pipeline",
        "process_id": process_id,
        "process": meta["name"],
        "accent": meta["accent"],
        "status": machine,
        "status_raw": pr.status,
        "period": "—",
        "started": _fmt_dt(started),
        "completed": _fmt_dt(pr.completed_at),
        "duration": _fmt_duration(pr.started_at, pr.completed_at),
        "rows": str(pr.rows_synced or "—"),
        "by": pr.triggered_by or "—",
        "related_docs": related,
        "_sort": str(started or ""),
    }


def _close_list_row(cr):
    meta = PROCESSES["consolidation"]
    started = cr.started_at or cr.creation
    machine = _close_machine(cr.status)
    period = f"FY{cr.fiscal_year} · P{cr.fiscal_period}" if cr.fiscal_period else f"FY{cr.fiscal_year}"
    return {
        "id": cr.name,
        "kind": "close",
        "run_type": "assertion",
        "process_id": "consolidation",
        "process": meta["name"],
        "accent": meta["accent"],
        "status": machine,
        "status_raw": cr.status,
        "period": period,
        "title": cr.title or cr.name,
        "started": _fmt_dt(started),
        "completed": _fmt_dt(cr.completed_at),
        "duration": _fmt_duration(cr.started_at, cr.completed_at, cr.duration_seconds),
        "rows": str(cr.total or "—"),
        "by": cr.triggered_by or "—",
        "related_docs": _related_docs_close(cr.name, cr.pipeline_run),
        "_sort": str(started or ""),
    }


def _pbr_run_detail(name, process_id):
    if not frappe.db.exists("Build Approval", name):
        frappe.throw(f"Run not found: {name}")
    pbr = frappe.get_doc("Build Approval", name)
    if pbr.build_scope != PROCESSES["budgeting"]["build_scope"]:
        frappe.throw("Not a budget run")

    pipes = frappe.get_all(
        "Pipeline Run",
        filters={"pipeline_build_request": pbr.name},
        fields=["name"],
        order_by="creation desc",
    )
    run = None
    if pipes:
        run = _serialize_pipeline_run(pipes[0].name)
    else:
        run = {
            "kind": "pbr",
            "name": pbr.name,
            "status": pbr.workflow_state,
            "machine_status": _pbr_machine(pbr.workflow_state),
            "started_at": pbr.started_at,
            "completed_at": pbr.completed_at,
            "elapsed_ms": int((pbr.duration_seconds or 0) * 1000),
            "steps": _pbr_workflow_steps(pbr.workflow_state),
            "logs": _logs_from_text(pbr.error_message),
            "rows": "—",
            "step_done": 0,
            "step_total": len(_pbr_workflow_steps(pbr.workflow_state)),
        }

    row = _pbr_list_row(pbr, process_id)
    return _run_detail_envelope(row, run)


def _pipeline_run_detail(name, process_id):
    if not frappe.db.exists("Pipeline Run", name):
        frappe.throw(f"Run not found: {name}")
    pr = frappe.db.get_value(
        "Pipeline Run",
        name,
        ["name", "status", "started_at", "completed_at", "rows_synced", "triggered_by", "pipeline_build_request", "creation"],
        as_dict=True,
    )
    if not _pipeline_belongs_to_forecast(pr.pipeline_build_request):
        frappe.throw("Not a forecast run")
    row = _pipeline_list_row(pr, process_id)
    run = _serialize_pipeline_run(name)
    return _run_detail_envelope(row, run)


_ORCH_STATE = {
    "Success": "done", "Failed": "error", "Cancelled": "error",
    "Running": "running", "Pending": "pending", "Skipped": "pending",
}


def _orchestrator_run_detail(name, process_id):
    """Drill-down for an orchestrator build run — maps the Pipeline Run's typed
    child steps (step_id/step_type/status/output/error) into the detail shape, so
    a consolidation build shows the same steps the Execute timeline does."""
    if not frappe.db.exists("Pipeline Run", name):
        frappe.throw(f"Run not found: {name}")
    doc = frappe.get_doc("Pipeline Run", name)
    pr = frappe._dict({
        f: doc.get(f) for f in (
            "name", "status", "started_at", "completed_at", "rows_synced",
            "triggered_by", "pipeline_build_request", "fiscal_year", "fiscal_period",
            "creation",
        )
    })
    row = _consolidation_build_row(pr)
    steps, logs = [], []
    for i, s in enumerate(doc.steps or [], start=1):
        state = _ORCH_STATE.get(s.status, "pending")
        steps.append({
            "num": f"{i:02d}",
            "name": s.step_id or s.step_type or "",
            "detail": s.step_type or "",
            "rows": str(s.rows or ""),
            "state": state,
            "pct": 100 if state in ("done", "error") else 0,
            "duration_ms": 0,
        })
        if s.output:
            logs.append(s.output)
        if s.error:
            logs.append(s.error)
    done = sum(1 for x in steps if x["state"] == "done")
    run = {
        "kind": "pipeline",
        "name": doc.name,
        "status": doc.status,
        "machine_status": _pipeline_machine(doc.status),
        "started_at": doc.started_at,
        "completed_at": doc.completed_at,
        "elapsed_ms": int((doc.get("duration_seconds") or 0) * 1000),
        "steps": steps,
        "logs": _logs_from_text("\n".join(logs)),
        "rows": str(doc.get("rows_synced") or 0),
        "step_done": done,
        "step_total": len(steps),
        "period": row["period"],
    }
    return _run_detail_envelope(row, run)


def _close_run_detail(name, process_id):
    if not frappe.db.exists("Period Close", name):
        frappe.throw(f"Run not found: {name}")
    cr = frappe.db.get_value(
        "Period Close",
        name,
        [
            "name", "status", "title", "fiscal_year", "fiscal_period", "pipeline_run",
            "started_at", "completed_at", "duration_seconds", "total", "triggered_by", "creation",
        ],
        as_dict=True,
    )
    row = _close_list_row(cr)
    run = _latest_close_run_for(name)
    return _run_detail_envelope(row, run)


def _latest_close_run_for(name):
    doc = frappe.get_doc("Period Close", name)
    steps = []
    for i, res in enumerate(doc.results or [], start=1):
        st = "done" if res.status == "Pass" else ("error" if res.status in ("Fail", "Error") else "pending")
        steps.append({
            "num": f"{i:02d}",
            "name": res.assertion or res.name,
            "detail": res.dimension or "",
            "rows": str(res.rows_failed or ""),
            "state": st,
            "pct": 100 if st == "done" else (0 if st == "pending" else 100),
            "duration_ms": 0,
        })
    done = sum(1 for s in steps if s["state"] == "done")
    return {
        "kind": "close",
        "name": doc.name,
        "status": doc.status,
        "machine_status": _close_machine(doc.status),
        "started_at": doc.started_at,
        "completed_at": doc.completed_at,
        "elapsed_ms": int((doc.duration_seconds or 0) * 1000),
        "steps": steps,
        "logs": _logs_from_text(doc.log),
        "rows": str(doc.total or 0),
        "step_done": done,
        "step_total": len(steps) or doc.total or 0,
        "period": f"FY{doc.fiscal_year} · P{doc.fiscal_period}" if doc.fiscal_period else f"FY{doc.fiscal_year}",
    }


def _run_detail_envelope(list_row, run):
    detail = {**list_row}
    detail.pop("_sort", None)
    detail["run"] = run
    detail["machine_status"] = run.get("machine_status") or list_row.get("status")
    return detail


def _pbr_workflow_steps(state):
    phases = ["Draft", "Pending Review", "Approved", "Running", "Completed"]
    if state in ("Failed", "Cancelled"):
        return [{"num": "01", "name": state, "detail": "Workflow", "rows": "", "state": "error", "pct": 100}]
    idx = phases.index(state) if state in phases else 0
    steps = []
    for i, phase in enumerate(phases, start=1):
        if i - 1 < idx:
            st = "done"
        elif phase == state:
            st = "running" if state == "Running" else "done"
        else:
            st = "pending"
        steps.append({
            "num": f"{i:02d}",
            "name": phase,
            "detail": "Governance",
            "rows": "",
            "state": st,
            "pct": 100 if st == "done" else (50 if st == "running" else 0),
        })
    return steps


def _related_docs_pbr(pbr, pipeline_rows):
    docs = [_doc_link("Build Approval", pbr.name, "primary")]
    for row in pipeline_rows:
        docs.append(_doc_link("Pipeline Run", row.name, "execution"))
    if pbr.trigger_doctype and pbr.trigger_docname:
        docs.append(_doc_link(pbr.trigger_doctype, pbr.trigger_docname, "trigger"))
    fy = _current_fiscal_year()
    cycle = frappe.db.get_value("Budget Cycle", {"fiscal_year": fy}, "name")
    if cycle:
        docs.append(_doc_link("Budget Cycle", cycle, "context"))
    return docs


def _related_docs_pipeline(pipe_name, pbr_name):
    docs = [_doc_link("Pipeline Run", pipe_name, "primary")]
    if pbr_name:
        docs.append(_doc_link("Build Approval", pbr_name, "upstream"))
        pbr = frappe.db.get_value(
            "Build Approval",
            pbr_name,
            ["trigger_doctype", "trigger_docname"],
            as_dict=True,
        )
        if pbr and pbr.trigger_doctype and pbr.trigger_docname:
            docs.append(_doc_link(pbr.trigger_doctype, pbr.trigger_docname, "trigger"))
    return docs


def _related_docs_close(close_name, pipeline_run):
    docs = [_doc_link("Period Close", close_name, "primary")]
    if pipeline_run:
        docs.append(_doc_link("Pipeline Run", pipeline_run, "upstream"))
        pbr = frappe.db.get_value("Pipeline Run", pipeline_run, "pipeline_build_request")
        if pbr:
            docs.append(_doc_link("Build Approval", pbr, "upstream"))
    return docs


def _desk_path(doctype, name=None):
    slug = (doctype or "").lower().replace(" ", "-")
    if name:
        return f"/app/{slug}/{name}"
    return f"/app/{slug}"


def _doc_link(doctype, name, role):
    return {
        "doctype": doctype,
        "name": name,
        "role": role,
        "label": doctype,
        "path": _desk_path(doctype, name),
    }


def _fmt_dt(value):
    return frappe.format(value, {"fieldtype": "Datetime"}) if value else "—"


def _fmt_duration(started_at, completed_at, duration_seconds=None):
    if duration_seconds:
        secs = float(duration_seconds)
    elif completed_at and started_at:
        secs = (get_datetime(completed_at) - get_datetime(started_at)).total_seconds()
    else:
        return "—"
    if secs < 120:
        return f"{secs:.1f}s"
    return f"{int(secs // 60)}m {int(secs % 60)}s"


def _logs_from_text(text):
    if not text:
        return []
    logs = []
    for line in (text or "").splitlines()[-30:]:
        level = "error" if "error" in line.lower() or "fail" in line.lower() else "info"
        if "✓" in line or "complete" in line.lower():
            level = "ok"
        logs.append({"t": "", "level": level, "text": line[:500]})
    return logs


def _scenario_options():
    return [
        row.scenario_id
        for row in frappe.get_all(
            "Scenario Definition",
            filters={"is_active": 1},
            fields=["scenario_id"],
            order_by="scenario_id",
        )
    ]


def _period_options(fy):
    periods = frappe.get_all(
        "Fiscal Period",
        fields=["fiscal_period", "label"],
        order_by="fiscal_period",
        limit=14,
    )
    if periods:
        return [f"FY{fy} · {p.label or ('P' + str(p.fiscal_period))}" for p in periods if 1 <= (p.fiscal_period or 0) <= 12]
    return [f"FY{fy}"]
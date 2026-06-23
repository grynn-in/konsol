"""Konsol Control — operator dashboard API.

Aggregates readiness checks, run status, budget layers, and history for the
three close processes: Budgeting, Forecasting, and Consolidation.
"""
from __future__ import annotations

import frappe
from frappe.utils import add_to_date, get_datetime, now_datetime, today

from konsol.epm.doctype.budget_sheet.budget_sheet import LAYER_ROLES

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
        "desc": "Group close, IC elimination, assertions, and sign-off.",
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
    return bool(frappe.db.exists(doctype, filters or {}))


def _current_fiscal_year():
    return frappe.utils.getdate(today()).year


@frappe.whitelist()
def get_snapshot():
    """Full control-plane state for the Konsol Control desk page."""
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
        "history": _history(),
        "scenarios": _scenario_options(),
        "periods": _period_options(fy),
    }


@frappe.whitelist()
def start_process(process_id, fiscal_year=None, fiscal_period=None):
    """Kick off the run for a close process."""
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
        from konsol.consolidation.doctype.close_run.close_run import trigger_close_run
        name = trigger_close_run(fiscal_year=fy, fiscal_period=fp)
        return {"ok": True, "run_kind": "close", "name": name}

    # budgeting — governed scenarios build
    scope = PROCESSES[pid]["build_scope"]
    pbr = frappe.get_doc({
        "doctype": "Pipeline Build Request",
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
        return _latest_close_run()
    if process_id == "forecasting":
        return _latest_pipeline_run(scope=None)
    return _latest_pbr_run(PROCESSES[process_id]["build_scope"])


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
        pbr_scope = frappe.db.get_value("Pipeline Build Request", row.pipeline_build_request, "build_scope")
        if pbr_scope != scope:
            return None
    if not scope and row.pipeline_build_request:
        pbr_scope = frappe.db.get_value("Pipeline Build Request", row.pipeline_build_request, "build_scope")
        if pbr_scope in ("scenarios", "consolidation"):
            return None
    return _serialize_pipeline_run(row.name)


def _latest_pbr_run(scope):
    pbr = frappe.get_all(
        "Pipeline Build Request",
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
        "Close Run",
        fields=["name", "status", "fiscal_year", "fiscal_period", "started_at", "completed_at",
                "duration_seconds", "total", "passed", "failed", "triggered_by", "log"],
        order_by="creation desc",
        limit=1,
    )
    if not rows:
        return None
    row = rows[0]
    doc = frappe.get_doc("Close Run", row.name)
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
        "Failure": "error",
        "Running": "running",
        "Pending": "pending",
        "Skipped": "done",
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
    n += frappe.db.count("Close Run", {"status": "Green", "completed_at": [">=", start]})
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


def _history(limit=20):
    rows = []
    for pr in frappe.get_all(
        "Pipeline Run",
        fields=["name", "status", "started_at", "completed_at", "rows_synced", "triggered_by", "pipeline_build_request", "creation"],
        order_by="creation desc",
        limit=limit,
    ):
        proc, accent = _process_for_pipeline(pr.pipeline_build_request)
        rows.append(_history_row(proc, accent, pr, "pipeline"))
    for cr in frappe.get_all(
        "Close Run",
        fields=["name", "status", "fiscal_year", "fiscal_period", "started_at", "completed_at",
                "duration_seconds", "total", "triggered_by", "creation"],
        order_by="creation desc",
        limit=limit,
    ):
        rows.append(_history_row(
            "Consolidation", "#2f7d4f", cr, "close",
            period=f"FY{cr.fiscal_year} · P{cr.fiscal_period}" if cr.fiscal_period else f"FY{cr.fiscal_year}",
            rows=str(cr.total or "—"),
        ))
    rows.sort(key=lambda r: r.get("_sort") or "", reverse=True)
    for r in rows:
        r.pop("_sort", None)
    return rows[:limit]


def _process_for_pipeline(pbr_name):
    if not pbr_name:
        return "Forecasting", "#0e8f84"
    scope = frappe.db.get_value("Pipeline Build Request", pbr_name, "build_scope")
    for pid, meta in PROCESSES.items():
        if meta.get("build_scope") == scope:
            return meta["name"], meta["accent"]
    return "Pipeline", "#0e8f84"


def _history_row(process, accent, doc, kind, period=None, rows=None):
    started = doc.started_at or getattr(doc, "creation", None)
    dur = ""
    if doc.completed_at and doc.started_at:
        secs = (get_datetime(doc.completed_at) - get_datetime(doc.started_at)).total_seconds()
        dur = f"{secs:.1f}s" if secs < 120 else f"{int(secs // 60)}m {int(secs % 60)}s"
    status = doc.status
    if kind == "close":
        status = {"Green": "done", "Red": "error", "Error": "error"}.get(status, status.lower())
    elif status == "Completed":
        status = "done"
    elif status == "Failed":
        status = "error"
    return {
        "process": process,
        "accent": accent,
        "period": period or "—",
        "started": frappe.format(started, {"fieldtype": "Datetime"}) if started else "—",
        "duration": dur or "—",
        "status": status,
        "rows": rows if rows is not None else str(getattr(doc, "rows_synced", None) or "—"),
        "by": doc.triggered_by or "—",
        "_sort": str(started or ""),
    }


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
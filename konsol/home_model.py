"""The rules behind the Konsol home, kept free of Frappe so they test on a host.

The home (F7, decided 12 Sep 2026) is one workspace for the whole close: a
fiscal navigator (years holding OPN, P01-P12 and CLS), one month's close as
eight ordered stages, and a work queue shaped by the viewer's roles. This
module decides states and order; `konsol.home_api` gathers the rows.

State words are the ones konsol-exec already renders (constants.js STATUS):
done, running, paused, error, incomplete, ready, waiting, idle.
"""
import datetime

MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")

#: (role, job title) in display priority. The screen shows titles; the code
#: checks roles. Budget Submitter is the old name for the base-layer owner.
TITLES = (
    ("EPM Admin", "Close Lead"),
    ("EPM Analyst", "Group Accountant"),
    ("Entity Accountant", "Entity Accountant"),
    ("Budget Submitter", "Entity Accountant"),
    ("Budget Manager", "Budget Reviewer"),
    ("Budget Controller", "Budget Reviewer"),
    ("Budget Approver", "Budget Reviewer"),
    ("EPM User", "Viewer"),
    ("System Manager", "System"),
)

#: The month's close, in order. Numbering is real: each stage feeds the next.
STAGES = (
    ("source", "Source data"),
    ("trial_balances", "Trial balances"),
    ("ownership", "Ownership & rates"),
    ("intercompany", "Intercompany"),
    ("adjustments", "Adjustments"),
    ("consolidate", "Consolidate"),
    ("assertions", "Assertions"),
    ("signoff", "Sign off"),
)

#: Which roles own each stage, so the lane can tint "yours".
STAGE_OWNERS = {
    "source": ("System Manager",),
    "trial_balances": ("Entity Accountant", "Budget Submitter"),
    "ownership": ("EPM Analyst",),
    "intercompany": ("EPM Analyst",),
    "adjustments": ("EPM Analyst", "EPM Admin"),
    "consolidate": ("EPM Admin",),
    "assertions": ("EPM Admin",),
    "signoff": ("EPM Admin",),
}

#: Most urgent first. A queue is sorted by this, then kept in insertion order.
STATE_RANK = {"error": 0, "paused": 1, "incomplete": 2, "ready": 3, "running": 4,
              "idle": 5, "waiting": 6, "done": 7}


def job_titles(roles):
    roles = set(roles or ())
    out = []
    for role, title in TITLES:
        if role in roles and title not in out:
            out.append(title)
    return out


def initials(name):
    parts = [p for p in str(name or "").replace("@", " ").replace(".", " ").split() if p]
    return "".join(p[0] for p in parts[:2]).upper() or "?"


def period_code(p):
    if p == 0:
        return "OPN"
    if p == 13:
        return "CLS"
    return f"P{p:02d}"


def period_label(fy, p):
    if p == 0:
        return "Opening balances"
    if p == 13:
        return "Year-end close"
    return f"{MONTHS[p - 1]} {fy}"


def period_start(fy, p):
    """First day a period covers. Period P of FY Y is the month starting Y-P-01
    (the warehouse's build_date_from_year_period); OPN sits at the start of the
    year and CLS at its last day."""
    if p == 0:
        return datetime.date(fy, 1, 1)
    if p == 13:
        return datetime.date(fy, 12, 31)
    return datetime.date(fy, p, 1)


def period_state(status, start, today):
    """Navigator state: locked, closed, open, or future (not started yet)."""
    if status == "Locked":
        return "locked"
    if status == "Closed":
        return "closed"
    if start > today:
        return "future"
    return "open"


def year_kind(fy, current):
    if fy < current:
        return "past"
    if fy == current:
        return "current"
    return "planning"


def stage(stage_id, state, summary, **extra):
    n = next(i for i, (sid, _) in enumerate(STAGES, start=1) if sid == stage_id)
    return {"id": stage_id, "n": n, "label": dict(STAGES)[stage_id], "state": state,
            "summary": summary, "owners": list(STAGE_OWNERS[stage_id]), **extra}


def source_stage(connectors):
    live = [c for c in connectors if c.get("enabled")]
    if not live:
        return stage("source", "idle", "No connectors")
    ok = sum(1 for c in live if c.get("last_sync_status") == "Success")
    failed = sum(1 for c in live if c.get("last_sync_status") == "Failed")
    running = sum(1 for c in live if c.get("last_sync_status") == "Running")
    summary = f"{ok} of {len(live)} synced"
    if failed:
        return stage("source", "error", f"{failed} failed · {summary}", failed=failed)
    if running:
        return stage("source", "running", summary)
    return stage("source", "done" if ok == len(live) else "incomplete", summary)


def tb_stage(expected, submitted, drafts, via_connector=()):
    """Trial balances owed by entities in the close that no connector feeds."""
    expected, submitted, drafts = set(expected), set(submitted), set(drafts)
    via = len(set(via_connector))
    n = len(expected)
    if not n:
        if via:
            return stage("trial_balances", "done", f"All {via} via connectors", missing=[])
        return stage("trial_balances", "idle", "No entities in the close", missing=[])
    got = len(expected & submitted)
    missing = sorted(expected - submitted)
    summary = f"{got} of {n} in"
    if not missing:
        return stage("trial_balances", "done", summary, missing=[])
    pending = len(expected & drafts - submitted)
    if pending:
        summary += f" · {pending} draft"
    return stage("trial_balances", "incomplete", summary, missing=missing)


def ownership_stage(uncovered, ownership_drafts, rate_drafts):
    uncovered = sorted(set(uncovered))
    todo = ownership_drafts + rate_drafts
    if uncovered:
        return stage("ownership", "incomplete", f"{len(uncovered)} without ownership", missing=uncovered)
    if todo:
        return stage("ownership", "incomplete", f"{todo} to submit", missing=[])
    return stage("ownership", "done", "Complete", missing=[])


def ic_stage(submitted, drafts):
    if drafts:
        return stage("intercompany", "incomplete", f"{drafts} draft · {submitted} submitted")
    if submitted:
        return stage("intercompany", "done", f"{submitted} submitted")
    return stage("intercompany", "idle", "No balances")


def adjustments_stage(by_status):
    pending = by_status.get("Pending Approval", 0)
    drafts = by_status.get("Draft", 0)
    approved = by_status.get("Approved", 0)
    if pending:
        return stage("adjustments", "paused", f"{pending} to approve")
    if drafts:
        return stage("adjustments", "incomplete", f"{drafts} draft")
    if approved:
        return stage("adjustments", "done", f"{approved} approved")
    return stage("adjustments", "done", "None")


_BUILD_STATE = {"Pending Review": "paused", "Approved": "running", "Running": "running",
                "Completed": "done", "Failed": "error", "Cancelled": "idle", "Draft": "idle"}


def consolidate_stage(build):
    if not build:
        return stage("consolidate", "idle", "No build yet")
    state = _BUILD_STATE.get(build.get("workflow_state"), "idle")
    words = {"paused": "Waiting for approval", "running": "Running", "done": "Built",
             "error": "Failed", "idle": build.get("workflow_state") or "Not started"}
    return stage("consolidate", state, words[state], build=build.get("name"))


def assertions_stage(run):
    if not run:
        return stage("assertions", "waiting", "Not run")
    status = run.get("status")
    if status in ("Queued", "Running"):
        return stage("assertions", "running", "Running", run=run.get("name"))
    if status == "Green":
        return stage("assertions", "done", f"Green · {run.get('passed') or 0} of {run.get('total') or 0}",
                     run=run.get("name"))
    if status in ("Red", "Error"):
        return stage("assertions", "error", f"{status} · {run.get('failed') or 0} failed", run=run.get("name"))
    return stage("assertions", "waiting", status or "Not run", run=run.get("name"))


def signoff_stage(status, assertions_state):
    if status in ("Closed", "Locked"):
        return stage("signoff", "done", status)
    if assertions_state == "done":
        return stage("signoff", "ready", "Ready to sign off")
    return stage("signoff", "waiting", "Waiting on assertions")


def ordered(items):
    """Dedupe by id (first wins: a person with two roles sees one row), then
    most urgent first."""
    seen, out = set(), []
    for item in items:
        if item["id"] in seen:
            continue
        seen.add(item["id"])
        out.append(item)
    return sorted(out, key=lambda i: STATE_RANK.get(i.get("state"), 99))

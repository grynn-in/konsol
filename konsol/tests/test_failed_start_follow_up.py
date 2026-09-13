"""A build that fails to start still builds the changes it absorbed (#140).

While a Build Approval is pending (Draft, Pending Review, Approved), the
debounce absorbs later requests for its scope. If the build then failed to
start, run_governed_build marked it Failed and those changes never reached
gold. Now a pending build is flagged too; only the start clears the flag; a
build that never starts (a start failure, or a lost job the reaper fails)
keeps it, and a follow-up, flagged in turn, is requested only while nothing
else is building, up to START_FAILURE_RETRIES per chain.

frappe is stubbed; the live A/B is in the PR."""
import ast
import contextlib
import importlib.util
import os
import sys
import types
from datetime import datetime, timedelta

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PREFIX = "Governed build could not start"
FAILED_MSG = f"{PREFIX}: A pipeline run is already active"
REAPED_MSG = "[reaper] marked Failed: approved but never started for >30m (its build job was lost)"
START = datetime(2026, 9, 13, 0, 0)


def _load(name, path, mods):
    saved = {m: sys.modules.get(m) for m in mods}
    sys.modules.update(mods)
    try:
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        _restore(saved)


def _restore(saved):
    for m, old in saved.items():
        if old is None:
            sys.modules.pop(m, None)
        else:
            sys.modules[m] = old


def _fn(path, name):
    with open(path) as f:
        tree = ast.parse(f.read())
    return next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == name)


REAPER = os.path.join(APP_DIR, "orchestrator", "reaper.py")
TASKS = os.path.join(APP_DIR, "tasks.py")
reaper = _load("reaper_140", REAPER, {})


# --- the decision -----------------------------------------------------------

def _row(**fields):
    row = dict(workflow_state="Failed", rebuild_requested=1, started_at=None, error_message=FAILED_MSG)
    row.update(fields)
    return row


def decide(so_far=0, **fields):
    return reaper.start_failure_follow_up(_row(**fields), so_far)


def test_a_flagged_build_that_failed_to_start_is_owed_a_follow_up():
    assert decide() == "request"


def test_an_unflagged_failed_start_is_owed_nothing():
    """Its own trigger is visible as a Failed row; only absorbed changes are hidden."""
    assert decide(rebuild_requested=0) is None


def test_the_sweep_takes_start_failures_only():
    # a build that ran (_finish_governed_build) or was reaped (reaper) is followed up there
    assert decide(error_message="dbt build failed (rc=1)") is None
    assert decide(error_message=REAPED_MSG) is None
    assert decide(started_at="2026-09-12 10:00:00") is None


def test_only_a_failed_row_is_owed():
    for state in ("Draft", "Pending Review", "Approved", "Running", "Completed", "Cancelled"):
        assert decide(workflow_state=state) is None, state


def test_the_chain_gives_up_at_the_cap():
    cap = reaper.START_FAILURE_RETRIES
    assert cap == 3
    assert [decide(so_far=n) for n in range(cap + 2)] == ["request"] * cap + ["give_up"] * 2


def test_a_row_that_never_started_is_capped_however_it_failed():
    d = reaper.follow_up_decision
    assert d(_row(rebuild_requested=0), 0) is None
    assert d(_row(error_message=REAPED_MSG), 2) == "request"
    assert d(_row(error_message=REAPED_MSG), 3) == "give_up", "a lost job counts like a start failure"
    assert d(_row(started_at=START, error_message="[reaper] running for >30m"), 99) == "request", (
        "a build that started is #129's follow-up, not a link in a chain")


def test_the_chain_counts_every_ancestor_that_never_started():
    link = dict(trigger_doctype="Build Approval")
    rows = {
        "BA-0": _row(name="BA-0", workflow_state="Completed", started_at=START, error_message=None,
                     trigger_doctype="Entity"),
        "BA-1": _row(name="BA-1", trigger_docname="BA-0", **link),          # follow-up of a build that ran
        "BA-2": _row(name="BA-2", trigger_docname="BA-1", **link),
        "BA-3": _row(name="BA-3", trigger_docname="BA-2", error_message=REAPED_MSG, **link),   # lost job
        "BA-4": _row(name="BA-4", trigger_docname="BA-3", **link),
        "BA-S": _row(name="BA-S", started_at=START, error_message="dbt build failed", trigger_docname="BA-4", **link),
        "BA-5": _row(name="BA-5", trigger_docname="BA-S", **link),          # its parent started
        "BA-X": _row(name="BA-X", trigger_docname="BA-Y", **link),
        "BA-Y": _row(name="BA-Y", trigger_docname="BA-X", **link),          # a cycle
    }
    length = lambda n: reaper.start_failure_chain_length(n, rows.get)
    assert [length(n) for n in ("BA-0", "BA-1", "BA-2", "BA-3", "BA-4")] == [0, 0, 1, 2, 3]
    assert length("BA-5") == 0
    assert length("BA-X") == 1 and length("BA-missing") == 0


def test_the_chain_stops_at_a_row_that_built_since():
    """A reset clears started_at, so a row that built in between is known by
    its Pipeline Runs (ever_built, set by the lookup) (#140 re-review)."""
    link = dict(trigger_doctype="Build Approval")
    rows = {
        "BA-A": _row(name="BA-A", trigger_doctype="Entity"),
        "BA-B": _row(name="BA-B", trigger_docname="BA-A", **link),
        "BA-C": _row(name="BA-C", trigger_docname="BA-B", **link),
        "BA-D": _row(name="BA-D", trigger_docname="BA-C", ever_built=True, **link),   # built, reset, failed
        "BA-E": _row(name="BA-E", trigger_docname="BA-D", **link),
    }
    length = lambda n: reaper.start_failure_chain_length(n, rows.get)
    assert length("BA-C") == 2
    assert length("BA-D") == 0, "it built since: its chain starts again"
    assert length("BA-E") == 0, "its parent built"


def test_which_pipeline_runs_show_a_build():
    shows = reaper.run_shows_a_build
    assert shows({"status": "Completed"})
    assert shows({"status": "Failed", "error_log": "Preflight failed: ClickHouse unhealthy"})
    assert shows({"status": "Failed", "error_log": "[reaper] Build Approval BA-1 reaped: running for >30m"})
    assert not shows({"status": "Failed", "error_log": FAILED_MSG}), "a start that failed after the run was created"
    assert shows({"status": "Queued"}), "a legacy run a start failure left Queued: err toward a retry"


def test_the_prefix_is_the_one_run_governed_build_writes():
    assert reaper.START_FAILURE_PREFIX == PREFIX
    with open(TASKS) as f:
        src = f.read()
    assert "from konsol.orchestrator.reaper import START_FAILURE_PREFIX" in src
    assert 'message = f"{START_FAILURE_PREFIX}: {exc}"' in src


# --- the flag ---------------------------------------------------------------

def _frappe_stub():
    frappe = types.ModuleType("frappe")
    frappe.db = types.SimpleNamespace(sql=lambda *a, **k: None)
    return frappe


def test_every_state_the_debounce_absorbs_into_is_flagged():
    """Draft and Pending Review too: a high-risk scope waits there for hours (#140 review)."""
    frappe = _frappe_stub()
    build_lock = _load("build_lock_140", os.path.join(APP_DIR, "build_lock.py"), {"frappe": frappe})
    for state, flagged in (("Draft", True), ("Pending Review", True), ("Approved", True), ("Running", True),
                           ("Completed", False), ("Failed", False), ("Cancelled", False)):
        calls = []
        frappe.db.sql = lambda q, *a, calls=calls: calls.append(q)
        build_lock.flag_running_build({"name": "BA-1", "workflow_state": state})
        assert bool(calls) is flagged, state
    with open(TASKS) as f:
        debounce = f.read().split("def request_build_for_scope")[1]
    assert "workflow_state IN ('Draft', 'Pending Review', 'Approved', 'Running')" in debounce
    assert set(build_lock.FLAGGED_STATES) == {"Draft", "Pending Review", "Approved", "Running"}


def _build_approval():
    frappe = _frappe_stub()
    frappe.session = types.SimpleNamespace(user="Administrator")

    def get_single(_):
        raise RuntimeError("no settings on the host")
    frappe.get_single = get_single
    model, document, utils = (types.ModuleType(n) for n in ("frappe.model", "frappe.model.document", "frappe.utils"))

    class Document:
        def __init__(self, before=None, **fields):
            self._before = before
            self.__dict__.update(fields)

        def get_doc_before_save(self):
            return self._before

        def is_new(self):
            return False

        def has_value_changed(self, field):
            return getattr(self._before, field, None) != getattr(self, field, None)

    document.Document = Document
    utils.now_datetime = lambda: None
    mods = {"frappe": frappe, "frappe.model": model, "frappe.model.document": document, "frappe.utils": utils}
    path = os.path.join(APP_DIR, "pipeline", "doctype", "build_approval", "build_approval.py")
    return _load("build_approval_140", path, mods).BuildApproval


def _save(before_state, before_flag, state, flag, scope="staging", error_message=None, started_at=None):
    BuildApproval = _build_approval()
    run = dict(started_at=started_at, completed_at=started_at and started_at + timedelta(minutes=5),
               duration_seconds=300.0 if started_at else 0)
    before = types.SimpleNamespace(workflow_state=before_state, rebuild_requested=before_flag, **run)
    doc = BuildApproval(before=before, workflow_state=state, rebuild_requested=flag, build_scope=scope,
                        requested_by="Administrator", error_message=error_message, **run)
    doc.before_save()
    return doc


def _reset(row):
    """An operator sends ``row`` back to Draft: the controller's before_save."""
    BuildApproval = _build_approval()
    doc = BuildApproval(before=types.SimpleNamespace(**row),
                        **dict(row, workflow_state="Draft", requested_by="Administrator"))
    doc.before_save()
    return {k: getattr(doc, k) for k in row}


def test_a_reset_of_a_row_that_ran_starts_it_over():
    """Its old start would hide its next start failure from the sweep and a
    lost job from the reaper. Its flag was spent by that run's finish, so it
    goes too (#140 re-review)."""
    doc = _save("Completed", 1, "Draft", 1, started_at=START)
    assert (doc.started_at, doc.completed_at, doc.duration_seconds) == (None, None, 0)
    assert doc.rebuild_requested == 0 and doc.workflow_state == "Approved"
    doc = _save("Failed", 1, "Draft", 0, error_message="dbt build failed (rc=1)", started_at=START)   # a stale form
    assert doc.rebuild_requested == 0 and doc.error_message is None and doc.started_at is None


def test_a_reset_of_a_finished_row_spends_its_flag():
    for state in ("Completed", "Failed"):
        doc = _save(state, 1, "Draft", 1, started_at=START)
        assert doc.rebuild_requested == 0 and doc.started_at is None, state


def test_a_reset_of_a_running_row_keeps_its_unspent_flag():
    """Its flag holds changes absorbed during the build that nothing has
    followed up: the build's own finish will fail its save (#140 re-review).
    The timing still goes, so the row can be reaped and followed up."""
    doc = _save("Running", 1, "Draft", 1, started_at=START)
    assert doc.rebuild_requested == 1 and doc.workflow_state == "Approved"
    assert (doc.started_at, doc.completed_at, doc.duration_seconds) == (None, None, 0)
    assert _save("Running", 1, "Draft", 0, started_at=START).rebuild_requested == 1, "a form opened before the flag"


def test_a_reset_of_a_cancelled_row_that_started_keeps_its_flag():
    """Chosen: kept. Cancelling dropped the row's changes without acting on
    its flag, so sending it back to run again owes them again."""
    doc = _save("Cancelled", 1, "Draft", 1, started_at=START)
    assert doc.rebuild_requested == 1 and doc.started_at is None


def test_only_a_reset_clears_a_run():
    doc = _save("Completed", 1, "Completed", 1, started_at=START)
    assert doc.started_at == START and doc.duration_seconds == 300.0 and doc.rebuild_requested == 1
    doc = _save("Running", 1, "Failed", 0, started_at=START)
    assert doc.started_at == START and doc.rebuild_requested == 1


def test_starting_spends_the_flag():
    assert _save("Approved", 1, "Running", 0).rebuild_requested == 0


def test_a_start_failure_keeps_the_flag():
    """The job reloads the row and saves it Failed; a flag set meanwhile survives."""
    assert _save("Approved", 1, "Failed", 0).rebuild_requested == 1


def test_approving_a_pending_review_build_keeps_what_it_absorbed():
    assert _save("Pending Review", 1, "Approved", 0, scope="consolidation").rebuild_requested == 1


def test_a_running_build_finishing_keeps_the_flag():
    assert _save("Running", 1, "Completed", 0).rebuild_requested == 1
    assert _save("Running", 1, "Running", 0).rebuild_requested == 1


def test_a_reset_to_draft_keeps_the_flag_and_clears_the_old_error():
    """The row hasn't built yet: if its next start fails, the changes it
    absorbed are still owed (#140 re-review). The old start-failure message
    goes, or the row would pass for a new start failure once reaped."""
    doc = _save("Failed", 1, "Draft", 1, error_message=FAILED_MSG)
    assert doc.rebuild_requested == 1 and doc.error_message is None
    assert doc.workflow_state == "Approved", "and it re-approves as before"
    doc = _save("Pending Review", 1, "Draft", 0, scope="consolidation")   # a form opened before the flag
    assert doc.rebuild_requested == 1 and doc.workflow_state == "Pending Review"


def test_run_governed_build_clears_the_flag_with_the_running_save():
    src = ast.unparse(_fn(TASKS, "run_governed_build"))
    start = src.index("doc.workflow_state = 'Running'")
    assert start < src.index("doc.rebuild_requested = 0") < src.index("doc.save(", start)
    assert src.count("rebuild_requested = 0") == 1, "only the start clears it"


def test_a_follow_up_is_flagged_by_every_caller():
    """A follow-up carries changes nothing has read; if it too never starts,
    it must be followed up in turn (#140 review)."""
    src = ast.unparse(_fn(TASKS, "request_build_for_scope"))
    assert "pbr.rebuild_requested = 1 if carries_changes else 0" in src
    assert src.index("pbr.rebuild_requested") < src.index("pbr.insert(")
    for path, fn in ((TASKS, "_finish_governed_build"), (REAPER, "reap_stale_build_approvals"),
                     (REAPER, "follow_up_failed_starts")):
        calls = [n for n in ast.walk(_fn(path, fn)) if isinstance(n, ast.Call)
                 and getattr(n.func, "id", "") == "request_build_for_scope"]
        assert calls and all(ast.unparse(k) == "carries_changes=True" for c in calls for k in c.keywords), fn


def test_the_reaper_spends_the_flag_of_the_row_it_follows_up():
    """Otherwise a reaped row that still looks like a start failure gets a
    second follow-up from the sweep (#140 review)."""
    src = ast.unparse(_fn(REAPER, "reap_stale_build_approvals"))
    loop = src[src.index("for row in follow_ups:"):]
    order = [loop.index(s) for s in ("lock_build_requests()", "SET rebuild_requested = 0 WHERE name = %s",
                                     "follow_up_decision(", "request_build_for_scope(")]
    assert order == sorted(order), order


# --- the reaper and the sweep -----------------------------------------------

class _D(dict):
    __getattr__ = dict.get


class FakeSite:
    """Build Approvals and Pipeline Runs, with commit/rollback of the flags,
    a clock, and the reaper's writes."""

    def __init__(self, rows, active_runs=0, request_fails=False, runs=None):
        self.rows = {r["name"]: dict(r) for r in rows}
        self.active_runs = active_runs
        self.runs = runs or {}   # Build Approval name -> its Pipeline Runs
        self.request_fails = request_fails
        self.now = START
        self.pending = {}
        self.requests = []
        self.errors = []
        self.locked = False
        self.rollbacks = 0

    def flag(self, name):
        return self.pending.get(name, self.rows[name]["rebuild_requested"])

    def get_all(self, doctype, filters=None, fields=None, limit=None, order_by=None):
        if doctype == "Pipeline Run":
            if "build_approval" in (filters or {}):   # the chain's evidence of a build
                return [dict(r) for r in self.runs.get(filters["build_approval"], [])]
            return [{"name": "PR-1"}] * self.active_runs
        states = filters["workflow_state"]
        if isinstance(states, list):   # ["in", [...]]: the busy check, or the reaper's candidates
            return [dict(r) for r in self.rows.values() if r["workflow_state"] in states[1]]
        assert filters == {"workflow_state": "Failed", "rebuild_requested": 1, "started_at": ["is", "not set"],
                           "error_message": ["like", f"{PREFIX}%"]}, filters
        return [dict(r) for r in self.rows.values() if self._failed_start(r) and r["rebuild_requested"]]

    @staticmethod
    def _failed_start(r):
        return (r["workflow_state"] == "Failed" and not r["started_at"]
                and (r.get("error_message") or "").startswith(PREFIX))

    def get_value(self, doctype, name, fields=None, as_dict=False):
        assert doctype == "Build Approval" and as_dict
        row = self.rows.get(name)
        return _D(row, rebuild_requested=self.flag(name)) if row else None

    def sql(self, query, params=None):
        q = " ".join(query.split())
        if q.startswith("SELECT name FROM `tabBuild Approval`"):
            # the claim: a locking read of the latest rows, under the build lock
            assert self.locked, "the build lock comes before the row locks"
            for part in ("FOR UPDATE", "build_scope = %s", "workflow_state = 'Failed'", "rebuild_requested = 1",
                         "started_at IS NULL", "error_message LIKE %s"):
                assert part in q, f"claim is missing {part!r}: {q}"
            scope, like = params
            assert like == f"{PREFIX}%"
            return tuple((n,) for n, r in self.rows.items()
                         if r["build_scope"] == scope and self._failed_start(r) and self.flag(n))
        if q == "UPDATE `tabBuild Approval` SET rebuild_requested = 0 WHERE name IN %s":
            for n in params[0]:
                self.pending[n] = 0
            return None
        if q == "UPDATE `tabBuild Approval` SET rebuild_requested = 0 WHERE name = %s":
            self.pending[params] = 0
            return None
        if q.startswith("UPDATE `tabBuild Approval` SET workflow_state = 'Failed'"):   # the reap
            note, now, _, name, state = params
            if self.rows[name]["workflow_state"] == state:
                self.rows[name].update(workflow_state="Failed", error_message=note, modified=now)
            return None
        if q.startswith("UPDATE `tabPipeline Run`"):
            return None
        raise AssertionError(q)

    def commit(self):
        for name, flag in self.pending.items():
            self.rows[name]["rebuild_requested"] = flag
        self.pending = {}
        self.locked = False

    def rollback(self):
        self.pending = {}
        self.locked = False
        self.rollbacks += 1

    def request_build_for_scope(self, scope, trigger_doctype, trigger_docname, carries_changes=False):
        if self.request_fails:
            raise RuntimeError("request failed")
        self.requests.append((scope, trigger_doctype, trigger_docname))
        name = f"BA-F{len(self.requests)}"
        # a low-risk scope auto-approves (the queue is busy until it builds);
        # a high-risk one waits in Pending Review
        self.rows[name] = dict(name=name, workflow_state="Approved" if scope == "staging" else "Pending Review",
                               rebuild_requested=1 if carries_changes else 0, started_at=None,
                               error_message=None, build_scope=scope, modified=self.now,
                               trigger_doctype=trigger_doctype, trigger_docname=trigger_docname)
        self.commit()

    def _run(self, fn):
        frappe = types.ModuleType("frappe")
        frappe.get_all = self.get_all
        frappe.db = types.SimpleNamespace(sql=self.sql, commit=self.commit, rollback=self.rollback,
                                          get_value=self.get_value)
        frappe.utils = types.SimpleNamespace(now_datetime=lambda: self.now)
        frappe.log_error = lambda title=None, message=None, **k: self.errors.append(title)
        frappe.logger = lambda: types.SimpleNamespace(info=lambda *a: None, warning=lambda *a: None)
        mods = {
            "frappe": frappe,
            "konsol.build_lock": types.SimpleNamespace(lock_build_requests=lambda: setattr(self, "locked", True)),
            "konsol.tasks": types.SimpleNamespace(request_build_for_scope=self.request_build_for_scope),
            "konsol.orchestrator.api": types.SimpleNamespace(ACTIVE_RUN_STATES=("Queued", "Running")),
        }
        saved = {m: sys.modules.get(m) for m in mods}
        sys.modules.update(mods)
        waiting = reaper._build_job_waiting
        reaper._build_job_waiting = lambda name: False   # the job is gone from RQ
        try:
            return fn()
        finally:
            reaper._build_job_waiting = waiting
            _restore(saved)

    def sweep(self):
        return self._run(reaper.follow_up_failed_starts)

    def tick(self):
        """A reaper tick, past the staleness window: reaps, then sweeps."""
        self.now += timedelta(minutes=reaper.STALE_BUILD_APPROVAL_MINUTES + 1)
        return self._run(reaper.reap_stale_build_approvals)

    def fails_to_start(self, name):
        """run_governed_build's except path: Failed, never started, flag kept."""
        self.rows[name].update(workflow_state="Failed", started_at=None, error_message=FAILED_MSG)

    def builds(self, name):
        """The start spends the flag; the build completes."""
        self.rows[name].update(workflow_state="Completed", started_at="2026-09-12 10:00:00", rebuild_requested=0)

    def newest(self):
        return self.requests and f"BA-F{len(self.requests)}"


def failed_start(name, scope="staging", flag=1):
    return dict(name=name, workflow_state="Failed", rebuild_requested=flag, started_at=None, modified=START,
                error_message=FAILED_MSG, build_scope=scope, trigger_doctype="ZZ", trigger_docname="ZZ-change")


def lost_job(name, flag=1):
    """Approved and flagged, and its build job never runs."""
    return dict(name=name, workflow_state="Approved", rebuild_requested=flag, started_at=None, modified=START,
                error_message=None, build_scope="staging", trigger_doctype="ZZ", trigger_docname="ZZ-change")


def test_a_failed_start_gets_one_flagged_follow_up():
    site = FakeSite([failed_start("BA-1")])
    assert site.sweep() == ["BA-1"]
    assert site.requests == [("staging", "Build Approval", "BA-1")]
    assert site.rows["BA-1"]["rebuild_requested"] == 0, "the flag is spent with the request"
    assert site.rows["BA-F1"]["rebuild_requested"] == 1, "the follow-up carries the changes"
    assert site.sweep() == [], "the follow-up is Approved: busy"
    site.builds("BA-F1")
    assert site.sweep() == [] and len(site.requests) == 1


def test_a_pending_review_build_that_failed_to_start_is_followed_up():
    """A high-risk scope absorbed the change while in Pending Review (#140 review)."""
    site = FakeSite([failed_start("BA-1", scope="consolidation")])
    assert site.sweep() == ["BA-1"]
    assert site.rows["BA-F1"]["workflow_state"] == "Pending Review" and site.rows["BA-F1"]["rebuild_requested"] == 1


def test_nothing_is_requested_while_a_pipeline_run_is_active():
    site = FakeSite([failed_start("BA-1")], active_runs=1)
    assert site.sweep() == [] and site.requests == []
    assert site.rows["BA-1"]["rebuild_requested"] == 1, "kept for the next sweep"
    site.active_runs = 0
    assert site.sweep() == ["BA-1"]


def test_one_sweep_never_lines_up_two_builds_against_each_other():
    site = FakeSite([failed_start("BA-1"), failed_start("BA-2", scope="actuals")])
    assert site.sweep() == ["BA-1"], "the first follow-up is Approved, so the queue is busy"
    assert site.rows["BA-2"]["rebuild_requested"] == 1
    site.builds("BA-F1")
    assert site.sweep() == ["BA-2"]


def test_a_follow_up_that_fails_to_start_is_retried_up_to_the_cap_then_logged():
    """The follow-up's job can line up behind a run started in the same
    scheduler tick and fail the same way (#140 review)."""
    site = FakeSite([failed_start("BA-1")])
    assert site.sweep() == ["BA-1"]
    for n in range(1, reaper.START_FAILURE_RETRIES):
        site.fails_to_start(f"BA-F{n}")
        assert site.sweep() == [f"BA-F{n}"], n
    last = f"BA-F{reaper.START_FAILURE_RETRIES}"
    site.fails_to_start(last)
    assert site.sweep() == [] and len(site.requests) == reaper.START_FAILURE_RETRIES
    assert site.errors == [f"Build Approval {last}: gave up after 3 follow-up builds failed to start"]
    assert site.rows[last]["rebuild_requested"] == 0, "spent, so the error is logged once"
    for _ in range(3):
        assert site.sweep() == []
    assert len(site.errors) == 1 and len(site.requests) == 3


def test_a_follow_up_whose_job_is_lost_every_time_stops_at_the_cap():
    """The follow-up is flagged, so the reaper follows up a lost one in turn;
    it counts toward the same cap (#140 re-review). 12 ticks = 6 hours."""
    site = FakeSite([lost_job("BA-1")])
    for _ in range(12):
        site.tick()
    assert len(site.requests) == 3, site.requests
    assert site.errors == ["Build Approval BA-F3: gave up after 3 follow-up builds failed to start"]
    assert all(r["workflow_state"] == "Failed" for r in site.rows.values())
    assert not any(r["rebuild_requested"] for r in site.rows.values())


def test_lost_jobs_and_start_failures_alternating_stop_at_the_cap():
    site = FakeSite([lost_job("BA-1")])
    for _ in range(12):
        newest = site.newest()
        if newest and site.rows[newest]["workflow_state"] == "Approved" and len(site.requests) % 2 == 0:
            site.fails_to_start(newest)   # this one's job runs, and its start fails
        site.tick()
    assert len(site.requests) == 3, site.requests
    assert len(site.errors) == 1 and "gave up after 3 follow-up builds" in site.errors[0]
    kinds = [(site.rows[n]["error_message"] or "")[:8] for n in ("BA-1", "BA-F1", "BA-F2", "BA-F3")]
    assert kinds == ["[reaper]", "[reaper]", PREFIX[:8], "[reaper]"], kinds


def test_a_reaped_running_build_is_still_followed_up():
    """#129: a change arrived while it ran. It started, so no chain applies."""
    site = FakeSite([dict(lost_job("BA-1"), workflow_state="Running", started_at=START)])
    site.tick()
    assert site.requests == [("staging", "Build Approval", "BA-1")] and site.errors == []
    assert site.rows["BA-1"]["rebuild_requested"] == 0 and site.rows["BA-F1"]["rebuild_requested"] == 1


def ran(name, **fields):
    """A row whose build started and finished; its finish requested the follow-up its flag asked for."""
    row = dict(name=name, workflow_state="Completed", rebuild_requested=1, started_at=START,
               completed_at=START + timedelta(minutes=5), duration_seconds=300.0, modified=START,
               error_message=None, build_scope="staging", trigger_doctype="ZZ", trigger_docname="ZZ-change")
    row.update(fields)
    return row


def test_a_row_that_ran_is_reset_and_fails_to_start_gets_one_follow_up():
    row = _reset(ran("BA-1"))
    assert row["workflow_state"] == "Approved" and row["started_at"] is None and row["rebuild_requested"] == 0
    site = FakeSite([row])
    site.rows["BA-1"]["rebuild_requested"] = 1   # a change absorbed while it waited to run again
    site.fails_to_start("BA-1")
    assert site.sweep() == ["BA-1"]
    assert site.sweep() == [] and site.requests == [("staging", "Build Approval", "BA-1")]


def test_a_row_that_ran_is_reset_and_loses_its_job_is_reaped():
    """With its old started_at, stale_build_approval_reason never reaped it,
    and it absorbed every request for its scope for ever (#125, #140 re-review)."""
    site = FakeSite([_reset(ran("BA-1", workflow_state="Failed", error_message="dbt build failed (rc=1)"))])
    assert site.tick() == ["BA-1"]
    assert site.requests == [], "its old flag was spent, and nothing new was absorbed"


def test_a_capped_chain_that_built_then_was_reset_is_followed_up_again():
    """A, B and C failed to start; D, the third follow-up, ran and Completed.
    D was reset, absorbed a change, and failed to start. Its trigger links
    still count three rows that never started, but D built in between."""
    chain = [dict(failed_start("BA-A"), rebuild_requested=0)]
    for name, parent in (("BA-B", "BA-A"), ("BA-C", "BA-B"), ("BA-D", "BA-C")):
        chain.append(dict(failed_start(name), trigger_doctype="Build Approval", trigger_docname=parent,
                          rebuild_requested=0 if name != "BA-D" else 1))
    site = FakeSite(chain, runs={"BA-D": [{"status": "Completed", "error_log": None}],
                                 "BA-C": [{"status": "Failed", "error_log": FAILED_MSG}]})
    assert site.sweep() == ["BA-D"] and site.errors == []
    control = FakeSite(chain)   # the same rows, no evidence that D built
    assert control.sweep() == [] and control.errors == [
        "Build Approval BA-D: gave up after 3 follow-up builds failed to start"]


def test_a_retry_that_builds_resets_the_chain():
    site = FakeSite([failed_start("BA-1")])
    site.sweep()
    site.builds("BA-F1")
    site.rows["BA-2"] = dict(failed_start("BA-2"), trigger_doctype="Build Approval", trigger_docname="BA-F1")
    assert site.sweep() == ["BA-2"], "its parent built, so its chain starts again"


def test_a_failed_request_keeps_the_flag_and_is_logged():
    site = FakeSite([failed_start("BA-1")], request_fails=True)
    assert site.sweep() == []
    assert site.rows["BA-1"]["rebuild_requested"] == 1
    assert site.errors == ["Follow-up build request for BA-1 failed"]


def test_a_row_another_sweep_already_claimed_is_skipped_and_rolled_back():
    site = FakeSite([failed_start("BA-1")])
    listed = site.get_all

    def claimed_after_listing(doctype, filters=None, **k):
        out = listed(doctype, filters, **k)
        if doctype == "Build Approval" and "error_message" in (filters or {}):
            site.rows["BA-1"]["rebuild_requested"] = 0   # the other sweep committed first
        return out
    site.get_all = claimed_after_listing
    assert site.sweep() == [] and site.requests == []
    assert site.rollbacks == 1 and not site.locked, "the skip releases the build lock"


def test_one_claim_covers_every_failed_start_of_the_scope():
    site = FakeSite([failed_start("BA-1"), failed_start("BA-2"), failed_start("BA-3", scope="actuals")])
    assert site.sweep() == ["BA-1"]
    assert len(site.requests) == 1
    assert site.rows["BA-1"]["rebuild_requested"] == 0 and site.rows["BA-2"]["rebuild_requested"] == 0
    assert site.rows["BA-3"]["rebuild_requested"] == 1, "another scope is its own claim"
    site.builds("BA-F1")
    assert site.sweep() == ["BA-3"]


def test_the_sweep_runs_with_the_scheduled_reaper():
    with open(REAPER) as f:
        body = f.read().split("def reap_stale_build_approvals")[1].split("\ndef ")[0]
    assert "follow_up_failed_starts()" in body
    assert body.index("for row in follow_ups:") < body.index("follow_up_failed_starts()")


# --- the start itself (tasks.run_governed_build) -----------------------------

ACTIVE = ("Queued", "Extracting", "Transforming", "Running")


class StartSite:
    """Documents with a committed store and one open transaction."""

    def __init__(self, fail_running_save=False, guard_error=None, cancel_meanwhile=False):
        self.committed = {("Build Approval", "BA-1"): dict(
            doctype="Build Approval", name="BA-1", workflow_state="Approved", rebuild_requested=1,
            started_at=None, completed_at=None, error_message=None, build_scope="staging",
            requested_by="Administrator")}
        self.pending = {}
        self.commits = []
        self.fail_running_save = fail_running_save
        self.guard_error = guard_error
        self.cancel_meanwhile = cancel_meanwhile
        self.warnings = []

    def doc(self, fields):
        site = self

        class Doc:
            def __init__(self, data):
                self.__dict__.update(data)

            def _fields(self):
                return dict(self.__dict__)

            def insert(self, **k):
                self.name = f"PR-{sum(1 for d, _ in site.committed if d == 'Pipeline Run') + 1}"
                site.pending[(self.doctype, self.name)] = self._fields()

            def save(self, **k):
                site.pending[(self.doctype, self.name)] = self._fields()
                if self.doctype == "Build Approval" and self.workflow_state == "Running" and site.fail_running_save:
                    # The row is written, then a later step of the save fails,
                    # as an after-save hook would: the open transaction holds
                    # a Running row with started_at set and the flag cleared.
                    if site.cancel_meanwhile:   # an operator cancelled it meanwhile
                        site.committed[(self.doctype, self.name)]["workflow_state"] = "Cancelled"
                    raise RuntimeError("Document has been modified after you have opened it")

            def reload(self):
                key = (self.doctype, self.name)
                # a read sees the transaction's own writes, as MariaDB's does
                self.__dict__.update(site.pending.get(key) or site.committed[key])

        return Doc(fields)

    def get_doc(self, arg, name=None):
        return self.doc(arg if isinstance(arg, dict) else dict(self.committed[(arg, name)]))

    def commit(self):
        self.commits.append(sorted(self.pending))
        self.committed.update(self.pending)
        self.pending = {}

    def rollback(self):
        self.pending = {}

    def active_runs(self):
        return [k for k, r in self.committed.items() if k[0] == "Pipeline Run" and r["status"] in ACTIVE]

    def start(self, api="stub"):
        frappe = types.ModuleType("frappe")
        frappe.get_doc = self.get_doc
        frappe.db = types.SimpleNamespace(commit=self.commit, rollback=self.rollback)
        frappe.utils = types.SimpleNamespace(now_datetime=lambda: START)
        frappe.session = types.SimpleNamespace(user="Administrator")
        frappe.logger = lambda: types.SimpleNamespace(info=lambda *a: None, warning=self.warnings.append)

        def guard():
            if self.guard_error:
                raise RuntimeError(self.guard_error)
        api_mod = types.SimpleNamespace(single_flight_lock=contextlib.nullcontext, _assert_no_active_run=guard)
        mods = {"frappe": frappe, "konsol.airbyte_service": types.SimpleNamespace(AirbyteClient=object),
                "konsol.orchestrator.reaper": reaper,
                "konsol.orchestrator.api": api_mod if api == "stub" else None}   # None: the import fails
        saved = {m: sys.modules.get(m) for m in mods}
        sys.modules.update(mods)
        try:
            spec = importlib.util.spec_from_file_location("tasks_140", TASKS)
            tasks = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(tasks)
            try:
                tasks.run_governed_build("BA-1")
            except Exception as exc:   # it re-raises, so the job records the failure
                return exc
            return None
        finally:
            _restore(saved)


def test_a_start_failure_fails_its_pipeline_run_in_the_same_commit():
    """The run was committed before the Running save failed. Left Queued, it
    would block every build, the follow-up's included (#140 re-review)."""
    site = StartSite(fail_running_save=True)
    assert site.start() is not None
    run = site.committed[("Pipeline Run", "PR-1")]
    assert run["status"] == "Failed" and run["error_log"].startswith(PREFIX) and run["completed_at"]
    ba = site.committed[("Build Approval", "BA-1")]
    assert ba["workflow_state"] == "Failed" and ba["error_message"] == run["error_log"]
    assert ba["rebuild_requested"] == 1 and not ba["started_at"], "the half-written Running save is dropped"
    assert site.commits[-1] == [("Build Approval", "BA-1"), ("Pipeline Run", "PR-1")], site.commits
    assert site.active_runs() == []


def test_a_start_failure_rolls_back_the_half_written_running_save():
    """Without the rollback, the failure path reloads the Running row the save
    wrote, and commits its start and its cleared flag: the absorbed changes
    are lost, and the row looks as if it ran."""
    site = StartSite(fail_running_save=True)
    site.start()
    ba = site.committed[("Build Approval", "BA-1")]
    assert ba["rebuild_requested"] == 1 and ba["started_at"] is None and ba["workflow_state"] == "Failed"


def test_a_row_cancelled_while_the_job_loaded_it_stays_cancelled():
    """Marked Failed with the start-failure message, a Cancelled row would be
    followed up. Its Pipeline Run still fails (#140 re-review)."""
    site = StartSite(fail_running_save=True, cancel_meanwhile=True)
    assert site.start() is not None
    ba = site.committed[("Build Approval", "BA-1")]
    assert ba["workflow_state"] == "Cancelled" and ba["error_message"] is None
    assert site.committed[("Pipeline Run", "PR-1")]["status"] == "Failed" and site.active_runs() == []
    assert len(site.warnings) == 1 and "it is Cancelled now, not Approved, so it is left Cancelled" in site.warnings[0]


def test_a_refused_start_leaves_no_pipeline_run():
    site = StartSite(guard_error="A pipeline run is already active")
    assert site.start() is not None
    assert [k for k in site.committed if k[0] == "Pipeline Run"] == []
    ba = site.committed[("Build Approval", "BA-1")]
    assert ba["workflow_state"] == "Failed" and ba["error_message"] == f"{PREFIX}: A pipeline run is already active"


def test_an_import_failure_is_a_start_failure():
    """Outside the try, it killed the job and left the row Approved for the reaper."""
    site = StartSite()
    assert isinstance(site.start(api="missing"), ImportError)
    ba = site.committed[("Build Approval", "BA-1")]
    assert ba["workflow_state"] == "Failed" and ba["error_message"].startswith(PREFIX)
    assert site.active_runs() == []


def test_the_reaper_returns_the_names_it_reaped():
    """A local named ``reaped`` in the follow-up loop once shadowed the list."""
    site = FakeSite([lost_job("BA-1")])
    assert site.tick() == ["BA-1"]
    assert site.tick() == ["BA-F1"]

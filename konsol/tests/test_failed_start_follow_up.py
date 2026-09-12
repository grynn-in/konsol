"""A build that fails to start still builds the changes it absorbed (#140).

While a Build Approval is Approved, the debounce absorbs later requests for its
scope. If the build then failed to start, run_governed_build marked it Failed
and those changes never reached gold. Now an Approved build is flagged too; the
start clears the flag, a start failure keeps it, and the reaper's sweep
requests one follow-up per failed start, only while nothing else is building.

frappe is stubbed; the live A/B is in the PR."""
import ast
import importlib.util
import os
import sys
import types

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PREFIX = "Governed build could not start"


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


reaper = _load("reaper_140", os.path.join(APP_DIR, "orchestrator", "reaper.py"), {})


# --- the decision -----------------------------------------------------------

def owes(busy=False, **fields):
    row = dict(workflow_state="Failed", rebuild_requested=1, started_at=None,
               error_message=f"{PREFIX}: A pipeline run is already active")
    row.update(fields)
    return reaper.owes_start_failure_follow_up(row, busy)


def test_a_flagged_build_that_failed_to_start_owes_a_follow_up():
    assert owes()


def test_nothing_is_owed_while_the_build_queue_is_busy():
    assert not owes(busy=True)


def test_an_unflagged_failed_start_owes_nothing():
    """Its own trigger is visible as a Failed row; only absorbed changes are hidden.
    This is also what stops a follow-up that fails to start from begetting another."""
    assert not owes(rebuild_requested=0)


def test_other_failures_already_requested_their_follow_up():
    # a build that ran (_finish_governed_build) or was reaped (reaper) requested its own
    assert not owes(error_message="dbt build failed (rc=1)")
    assert not owes(error_message="[reaper] marked Failed: approved but never started")
    assert not owes(started_at="2026-09-12 10:00:00")


def test_only_a_failed_row_owes():
    for state in ("Draft", "Pending Review", "Approved", "Running", "Completed", "Cancelled"):
        assert not owes(workflow_state=state), state


def test_the_prefix_is_the_one_run_governed_build_writes():
    assert reaper.START_FAILURE_PREFIX == PREFIX
    with open(os.path.join(APP_DIR, "tasks.py")) as f:
        src = f.read()
    assert 'doc.error_message = f"{START_FAILURE_PREFIX}: {exc}"' in src


# --- the flag ---------------------------------------------------------------

def _frappe_stub():
    frappe = types.ModuleType("frappe")
    frappe.db = types.SimpleNamespace(sql=lambda *a, **k: None)
    return frappe


def test_an_approved_or_running_build_is_flagged_and_a_pending_one_is_not():
    frappe = _frappe_stub()
    build_lock = _load("build_lock_140", os.path.join(APP_DIR, "build_lock.py"), {"frappe": frappe})
    for state, flagged in (("Draft", False), ("Pending Review", False), ("Approved", True), ("Running", True)):
        calls = []
        frappe.db.sql = lambda q, *a, calls=calls: calls.append(q)
        build_lock.flag_running_build({"name": "BA-1", "workflow_state": state})
        assert bool(calls) is flagged, state


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


def _saved_flag(before_state, before_flag, state, flag):
    BuildApproval = _build_approval()
    before = types.SimpleNamespace(workflow_state=before_state, rebuild_requested=before_flag)
    doc = BuildApproval(before=before, workflow_state=state, rebuild_requested=flag,
                        build_scope="staging", requested_by="Administrator")
    doc.before_save()
    return doc.rebuild_requested


def test_starting_spends_the_flag_an_approved_build_carried():
    assert _saved_flag("Approved", 1, "Running", 0) == 0


def test_a_start_failure_keeps_the_flag():
    """The job reloads the row and saves it Failed; a flag set meanwhile survives."""
    assert _saved_flag("Approved", 1, "Failed", 0) == 1


def test_a_running_build_finishing_keeps_the_flag():
    assert _saved_flag("Running", 1, "Completed", 0) == 1
    assert _saved_flag("Running", 1, "Running", 0) == 1


def test_run_governed_build_clears_the_flag_with_the_running_save():
    with open(os.path.join(APP_DIR, "tasks.py")) as f:
        tree = ast.parse(f.read())
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "run_governed_build")
    src = ast.unparse(fn)
    start = src.index("doc.workflow_state = 'Running'")
    assert start < src.index("doc.rebuild_requested = 0") < src.index("doc.save(", start)
    assert src.count("rebuild_requested = 0") == 1, "only the start clears it"


# --- the sweep --------------------------------------------------------------

class FakeSite:
    """Build Approvals and Pipeline Runs, with commit/rollback of the flag."""

    def __init__(self, rows, active_runs=0, request_fails=False):
        self.rows = {r["name"]: dict(r) for r in rows}
        self.active_runs = active_runs
        self.request_fails = request_fails
        self.pending = {}
        self.requests = []
        self.errors = []
        self.locks = []

    def get_all(self, doctype, filters=None, fields=None, limit=None, order_by=None):
        if doctype == "Pipeline Run":
            return [{"name": "PR-1"}] * self.active_runs
        states = filters["workflow_state"]
        if isinstance(states, list):   # ["in", [...]]
            return [r for r in self.rows.values() if r["workflow_state"] in states[1]]
        assert filters["error_message"] == ["like", f"{PREFIX}%"]
        return [dict(r) for r in self.rows.values()
                if r["workflow_state"] == "Failed" and r["rebuild_requested"]
                and (r.get("error_message") or "").startswith(PREFIX)]

    def sql(self, query, params=None):
        name = params
        if query.startswith("SELECT rebuild_requested"):
            assert self.locks, "the build lock comes before the row lock"
            flag = self.pending.get(name, self.rows[name]["rebuild_requested"])
            return ((flag,),)
        if "SET rebuild_requested = 0" in query:
            self.pending[name] = 0
            return None
        raise AssertionError(query)

    def commit(self):
        for name, flag in self.pending.items():
            self.rows[name]["rebuild_requested"] = flag
        self.pending = {}

    def rollback(self):
        self.pending = {}
        self.locks = []

    def request_build_for_scope(self, scope, trigger_doctype, trigger_docname):
        if self.request_fails:
            raise RuntimeError("request failed")
        self.requests.append((scope, trigger_doctype, trigger_docname))
        name = f"BA-F{len(self.requests)}"
        # a low-risk scope auto-approves: the queue is busy until it builds
        self.rows[name] = dict(name=name, workflow_state="Approved", rebuild_requested=0, started_at=None,
                               error_message=None, build_scope=scope)
        self.commit()
        self.locks = []

    def sweep(self):
        frappe = types.ModuleType("frappe")
        frappe.get_all = self.get_all
        frappe.db = types.SimpleNamespace(sql=self.sql, commit=self.commit, rollback=self.rollback)
        frappe.log_error = lambda title=None, **k: self.errors.append(title)
        frappe.logger = lambda: types.SimpleNamespace(info=lambda *a: None)
        mods = {
            "frappe": frappe,
            "konsol.build_lock": types.SimpleNamespace(lock_build_requests=lambda: self.locks.append(1)),
            "konsol.tasks": types.SimpleNamespace(request_build_for_scope=self.request_build_for_scope),
            "konsol.orchestrator.api": types.SimpleNamespace(ACTIVE_RUN_STATES=("Queued", "Running")),
        }
        saved = {m: sys.modules.get(m) for m in mods}
        sys.modules.update(mods)
        try:
            return reaper.follow_up_failed_starts()
        finally:
            _restore(saved)

    def build_finishes(self, name, state="Completed"):
        self.rows[name]["workflow_state"] = state


def failed_start(name, scope="staging", flag=1):
    return dict(name=name, workflow_state="Failed", rebuild_requested=flag, started_at=None,
                error_message=f"{PREFIX}: A pipeline run is already active", build_scope=scope)


def test_a_failed_start_gets_exactly_one_follow_up():
    site = FakeSite([failed_start("BA-1")])
    assert site.sweep() == ["BA-1"]
    assert site.requests == [("staging", "Build Approval", "BA-1")]
    assert site.rows["BA-1"]["rebuild_requested"] == 0, "the flag is spent with the request"
    site.build_finishes("BA-F1")
    assert site.sweep() == [] and len(site.requests) == 1


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
    site.build_finishes("BA-F1")
    assert site.sweep() == ["BA-2"]


def test_a_follow_up_that_also_fails_to_start_does_not_loop():
    site = FakeSite([failed_start("BA-1")])
    site.sweep()
    # the follow-up fails to start too, with nothing new absorbed: no flag
    site.rows["BA-F1"].update(failed_start("BA-F1", flag=0))
    for _ in range(3):
        assert site.sweep() == []
    assert len(site.requests) == 1


def test_a_failed_request_keeps_the_flag_and_is_logged():
    site = FakeSite([failed_start("BA-1")], request_fails=True)
    assert site.sweep() == []
    assert site.rows["BA-1"]["rebuild_requested"] == 1
    assert site.errors == ["Follow-up build request for BA-1 failed"]


def test_a_row_another_sweep_already_claimed_is_skipped():
    site = FakeSite([failed_start("BA-1")])
    listed = site.get_all

    def claimed_after_listing(*a, **k):
        out = listed(*a, **k)
        if a[0] == "Build Approval" and "error_message" in (k.get("filters") or {}):
            site.rows["BA-1"]["rebuild_requested"] = 0   # the other sweep committed first
        return out
    site.get_all = claimed_after_listing
    assert site.sweep() == [] and site.requests == []


def test_the_sweep_runs_with_the_scheduled_reaper():
    with open(os.path.join(APP_DIR, "orchestrator", "reaper.py")) as f:
        body = f.read().split("def reap_stale_build_approvals")[1].split("\ndef ")[0]
    assert "follow_up_failed_starts()" in body
    assert body.index("for row in follow_ups:") < body.index("follow_up_failed_starts()")

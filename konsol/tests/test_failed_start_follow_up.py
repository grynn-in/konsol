"""A build that fails to start still builds the changes it absorbed (#140).

While a Build Approval is pending (Draft, Pending Review, Approved), the
debounce absorbs later requests for its scope. If the build then failed to
start, run_governed_build marked it Failed and those changes never reached
gold. Now a pending build is flagged too; the start clears the flag, a start
failure keeps it, and the reaper's sweep requests a follow-up, flagged in turn,
only while nothing else is building, up to START_FAILURE_RETRIES per chain.

frappe is stubbed; the live A/B is in the PR."""
import ast
import importlib.util
import os
import sys
import types

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PREFIX = "Governed build could not start"
FAILED_MSG = f"{PREFIX}: A pipeline run is already active"


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


reaper = _load("reaper_140", os.path.join(APP_DIR, "orchestrator", "reaper.py"), {})
REAPER = os.path.join(APP_DIR, "orchestrator", "reaper.py")
TASKS = os.path.join(APP_DIR, "tasks.py")


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


def test_other_failures_already_requested_their_follow_up():
    # a build that ran (_finish_governed_build) or was reaped (reaper) requested its own
    assert decide(error_message="dbt build failed (rc=1)") is None
    assert decide(error_message="[reaper] marked Failed: approved but never started") is None
    assert decide(started_at="2026-09-12 10:00:00") is None


def test_only_a_failed_row_is_owed():
    for state in ("Draft", "Pending Review", "Approved", "Running", "Completed", "Cancelled"):
        assert decide(workflow_state=state) is None, state


def test_the_chain_gives_up_at_the_cap():
    cap = reaper.START_FAILURE_RETRIES
    assert cap == 3
    assert [decide(so_far=n) for n in range(cap + 2)] == ["request"] * cap + ["give_up"] * 2


def test_the_chain_length_counts_start_failed_ancestors_only():
    rows = {
        "BA-0": _row(name="BA-0", workflow_state="Completed", error_message=None, trigger_doctype="Entity"),
        "BA-1": _row(name="BA-1", trigger_doctype="Build Approval", trigger_docname="BA-0"),  # follow-up of a build that ran
        "BA-2": _row(name="BA-2", trigger_doctype="Build Approval", trigger_docname="BA-1"),
        "BA-3": _row(name="BA-3", trigger_doctype="Build Approval", trigger_docname="BA-2"),
        "BA-X": _row(name="BA-X", trigger_doctype="Build Approval", trigger_docname="BA-Y"),
        "BA-Y": _row(name="BA-Y", trigger_doctype="Build Approval", trigger_docname="BA-X"),  # a cycle
    }
    length = lambda n: reaper.start_failure_chain_length(n, rows.get)
    assert [length(n) for n in ("BA-0", "BA-1", "BA-2", "BA-3")] == [0, 0, 1, 2]
    assert length("BA-X") == 1 and length("BA-missing") == 0


def test_the_prefix_is_the_one_run_governed_build_writes():
    assert reaper.START_FAILURE_PREFIX == PREFIX
    with open(TASKS) as f:
        assert 'doc.error_message = f"{START_FAILURE_PREFIX}: {exc}"' in f.read()


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


def _save(before_state, before_flag, state, flag, scope="staging", error_message=None):
    BuildApproval = _build_approval()
    before = types.SimpleNamespace(workflow_state=before_state, rebuild_requested=before_flag)
    doc = BuildApproval(before=before, workflow_state=state, rebuild_requested=flag, build_scope=scope,
                        requested_by="Administrator", error_message=error_message)
    doc.before_save()
    return doc


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


def test_a_reset_to_draft_clears_the_flag_and_the_old_error():
    """A start-failure message left on a row that runs again would pass for a
    new start failure once the row is reaped (#140 review)."""
    doc = _save("Failed", 1, "Draft", 1, error_message=FAILED_MSG)
    assert doc.rebuild_requested == 0 and doc.error_message is None
    assert doc.workflow_state == "Approved", "and it re-approves as before"


def test_run_governed_build_clears_the_flag_with_the_running_save():
    src = ast.unparse(_fn(TASKS, "run_governed_build"))
    start = src.index("doc.workflow_state = 'Running'")
    assert start < src.index("doc.rebuild_requested = 0") < src.index("doc.save(", start)
    assert src.count("rebuild_requested = 0") == 1, "only the start clears it"


def test_a_follow_up_is_flagged_by_every_caller():
    """A follow-up carries changes nothing has read; if it too fails to start,
    the sweep must see it (#140 review)."""
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
                                     "request_build_for_scope(")]
    assert order == sorted(order), order


# --- the sweep --------------------------------------------------------------

class FakeSite:
    """Build Approvals and Pipeline Runs, with commit/rollback of the flags."""

    def __init__(self, rows, active_runs=0, request_fails=False):
        self.rows = {r["name"]: dict(r) for r in rows}
        self.active_runs = active_runs
        self.request_fails = request_fails
        self.pending = {}
        self.requests = []
        self.errors = []
        self.locked = False
        self.rollbacks = 0

    def flag(self, name):
        return self.pending.get(name, self.rows[name]["rebuild_requested"])

    def get_all(self, doctype, filters=None, fields=None, limit=None, order_by=None):
        if doctype == "Pipeline Run":
            return [{"name": "PR-1"}] * self.active_runs
        states = filters["workflow_state"]
        if isinstance(states, list):   # ["in", [...]]: the busy check
            return [r for r in self.rows.values() if r["workflow_state"] in states[1]]
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
        return dict(row) if row else None

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
                               error_message=None, build_scope=scope,
                               trigger_doctype=trigger_doctype, trigger_docname=trigger_docname)
        self.commit()

    def sweep(self):
        frappe = types.ModuleType("frappe")
        frappe.get_all = self.get_all
        frappe.db = types.SimpleNamespace(sql=self.sql, commit=self.commit, rollback=self.rollback,
                                          get_value=self.get_value)
        frappe.log_error = lambda title=None, message=None, **k: self.errors.append(title)
        frappe.logger = lambda: types.SimpleNamespace(info=lambda *a: None)
        mods = {
            "frappe": frappe,
            "konsol.build_lock": types.SimpleNamespace(lock_build_requests=lambda: setattr(self, "locked", True)),
            "konsol.tasks": types.SimpleNamespace(request_build_for_scope=self.request_build_for_scope),
            "konsol.orchestrator.api": types.SimpleNamespace(ACTIVE_RUN_STATES=("Queued", "Running")),
        }
        saved = {m: sys.modules.get(m) for m in mods}
        sys.modules.update(mods)
        try:
            return reaper.follow_up_failed_starts()
        finally:
            _restore(saved)

    def fails_to_start(self, name):
        """run_governed_build's except path: Failed, never started, flag kept."""
        self.rows[name].update(workflow_state="Failed", started_at=None, error_message=FAILED_MSG)

    def builds(self, name):
        """The start spends the flag; the build completes."""
        self.rows[name].update(workflow_state="Completed", started_at="2026-09-12 10:00:00", rebuild_requested=0)


def failed_start(name, scope="staging", flag=1):
    return dict(name=name, workflow_state="Failed", rebuild_requested=flag, started_at=None,
                error_message=FAILED_MSG, build_scope=scope, trigger_doctype="ZZ", trigger_docname="ZZ-change")


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


def test_a_reaped_row_is_never_taken_for_a_start_failure():
    """Reset to Draft clears the old message, so a reaped row's reads [reaper]."""
    assert decide(error_message="[reaper] marked Failed: approved but never started for >30m") is None


def test_the_sweep_runs_with_the_scheduled_reaper():
    with open(REAPER) as f:
        body = f.read().split("def reap_stale_build_approvals")[1].split("\ndef ")[0]
    assert "follow_up_failed_starts()" in body
    assert body.index("for row in follow_ups:") < body.index("follow_up_failed_starts()")

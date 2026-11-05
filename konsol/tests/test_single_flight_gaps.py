"""Only one dbt process at a time, on every path (konsol#170, option C).

Three paths got around the single-flight gate (``single_flight_lock`` +
``_assert_no_active_run``):

1. ``orchestrator.api.cancel_run`` set Cancelled on a run whose dbt was still
   running, so it stopped counting as active and another build could start.
2. ``schema_apply.apply_schema(run_dbt=1)`` enqueued
   ``tasks.run_dbt_build_async``: a dbt build with no lock, no active-run
   check and no Pipeline Run. It now requests a governed Build Approval
   (tested in test_publish_atomic.py) and the legacy job is gone.
3. ``assertion_run.trigger_close_run`` ran ``dbt test`` outside the lock, and
   an active Assertion Run did not count as active for a build.

Chosen under Deepak Pai's standing rule of 5 Nov 2026 (product,
configurable, not opinionated); HMD table on konsol#170.

``konsol.orchestrator.api`` and ``reaper`` import frappe inside their
functions, so a stub frappe in sys.modules is what they see. The host runner
restores sys.modules after this file.
"""
import ast
import os
import sys
import types

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ASSERTION_RUN = os.path.join(APP_DIR, "consolidation", "doctype", "assertion_run", "assertion_run.py")


class _ValidationError(Exception):
    pass


class _Row(dict):
    __getattr__ = dict.get

    def __setattr__(self, key, value):
        self[key] = value


class _Site:
    """Pipeline Runs and Assertion Runs by name, behind a stub frappe."""

    def __init__(self, pipeline_runs=(), assertion_runs=()):
        self.rows = {"Pipeline Run": {r["name"]: r for r in pipeline_runs},
                     "Assertion Run": {r["name"]: r for r in assertion_runs},
                     "Build Approval": {}}
        self.saved = []
        site = self
        frappe = types.ModuleType("frappe")
        frappe.ValidationError = _ValidationError

        def throw(msg, exc=None, **k):
            raise (exc or _ValidationError)(msg)
        frappe.throw = throw

        def get_all(doctype, filters=None, fields=None, limit=None, **k):
            out = []
            for row in site.rows.get(doctype, {}).values():
                ok = True
                for key, cond in (filters or {}).items():
                    if isinstance(cond, list) and cond[0] == "in":
                        ok = ok and row.get(key) in cond[1]
                    else:
                        ok = ok and row.get(key) == cond
                if ok:
                    out.append(_Row(name=row["name"]))
            return out[:limit] if limit else out
        frappe.get_all = get_all

        class _Doc(_Row):
            def save(self, **k):
                site.saved.append((self["name"], self["status"]))
                site.rows["Pipeline Run"][self["name"]].update(
                    {k: v for k, v in self.items() if k != "steps"})

        def get_doc(doctype, name, **k):
            row = site.rows[doctype][name]
            doc = _Doc(row)
            doc["steps"] = [_Row(s) for s in row.get("steps", [])]
            return doc
        frappe.get_doc = get_doc
        frappe.db = types.SimpleNamespace(commit=lambda: None, sql=lambda *a, **k: [[1]])
        self.frappe = frappe
        lifecycle = types.ModuleType("konsol.schema_lifecycle")
        lifecycle.check_epm_admin = lambda: None
        # RQ, as cancel_run asks it about a Transforming run: by default the
        # run's job is alive (a worker is building).
        bj = types.ModuleType("frappe.utils.background_jobs")
        bj.is_job_enqueued = lambda job_id: True
        utils = types.ModuleType("frappe.utils")
        utils.background_jobs = bj
        frappe.utils = utils
        self.mods = {"frappe": frappe, "konsol.schema_lifecycle": lifecycle,
                     "frappe.utils": utils, "frappe.utils.background_jobs": bj}

    def install(self):
        sys.modules.update(self.mods)


def _api():
    from konsol.orchestrator import api
    return api


def _refused(fn):
    try:
        fn()
    except _ValidationError as exc:
        return str(exc)
    raise AssertionError("not refused")


# --- 1. cancel while dbt runs -------------------------------------------------


def test_cancelling_a_run_whose_dbt_is_running_is_refused():
    site = _Site(pipeline_runs=[{"name": "ZZ-PR-1", "status": "Transforming"}])
    site.install()
    msg = _refused(lambda: _api().cancel_run("ZZ-PR-1"))
    assert "ZZ-PR-1" in msg, msg
    assert site.rows["Pipeline Run"]["ZZ-PR-1"]["status"] == "Transforming"
    assert site.saved == []


def test_cancelling_an_orchestrator_run_with_a_step_running_is_refused():
    site = _Site(pipeline_runs=[{"name": "ZZ-PR-2", "status": "Running", "steps": [
        {"step_id": "extract", "step_type": "airbyte_sync", "status": "Completed"},
        {"step_id": "build", "step_type": "dbt_build", "status": "Running"}]}])
    site.install()
    msg = _refused(lambda: _api().cancel_run("ZZ-PR-2"))
    assert "build" in msg, msg
    assert site.saved == []


def test_a_run_whose_dbt_has_not_started_can_still_be_cancelled():
    for status, steps in (("Queued", []), ("Extracting", []),
                          ("Running", [{"step_id": "build", "step_type": "dbt_build",
                                        "status": "Pending"}])):
        site = _Site(pipeline_runs=[{"name": "ZZ-PR-3", "status": status, "steps": steps}])
        site.install()
        assert _api().cancel_run("ZZ-PR-3") == "ZZ-PR-3"
        assert site.rows["Pipeline Run"]["ZZ-PR-3"]["status"] == "Cancelled", status


# --- 3. an Assertion Run is an active dbt process ------------------------------


def test_an_active_assertion_run_blocks_a_build():
    for status in ("Queued", "Running"):
        site = _Site(assertion_runs=[{"name": "ZZ-AR-1", "status": status}])
        site.install()
        msg = _refused(_api()._assert_no_active_run)
        assert "ZZ-AR-1" in msg, msg


def test_a_finished_assertion_run_blocks_nothing():
    site = _Site(assertion_runs=[{"name": "ZZ-AR-2", "status": "Green"}])
    site.install()
    _api()._assert_no_active_run()


def test_the_follow_up_sweep_waits_for_an_active_assertion_run():
    site = _Site(assertion_runs=[{"name": "ZZ-AR-3", "status": "Running"}])
    site.install()
    from konsol.orchestrator import reaper
    assert reaper._build_queue_busy() is True


def _function(path, name):
    with open(path) as f:
        tree = ast.parse(f.read())
    return next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name)


def test_starting_close_assertions_takes_the_single_flight_gate():
    """The check and the insert sit inside ``with single_flight_lock():``, as
    in start_run, so a build cannot slip in between."""
    fn = _function(ASSERTION_RUN, "trigger_close_run")
    withs = [n for n in ast.walk(fn) if isinstance(n, ast.With)
             and any(isinstance(i.context_expr, ast.Call)
                     and getattr(i.context_expr.func, "id", "") == "single_flight_lock"
                     for i in n.items)]
    assert withs, "trigger_close_run does not take single_flight_lock"
    body = ast.unparse(withs[0])
    assert "_assert_no_active_run()" in body, "the active-run check is not inside the lock"
    assert "doc.insert(" in body, "the insert is not inside the lock"


# --- 2. the ungoverned schema-apply build is gone -------------------------------


def test_no_code_queues_the_legacy_dbt_build():
    found = []
    for root, _dirs, files in os.walk(APP_DIR):
        if os.sep + "tests" in root:
            continue
        for name in files:
            if name.endswith(".py"):
                path = os.path.join(root, name)
                with open(path) as f:
                    src = f.read()
                if "run_dbt_build_async" in src or "_run_dbt_build_background" in src:
                    found.append(os.path.relpath(path, APP_DIR))
    assert not found, f"the ungated legacy dbt build is still referenced: {found}"


# --- review of #392 -------------------------------------------------------------

# 2. run_close_assertions starts only a Queued run.


def _run_close_assertions_ns(status):
    """run_close_assertions compiled alone, against a stub frappe."""
    events = {"get_value": [], "saved": [], "popen": []}
    frappe = types.SimpleNamespace()

    class _Doc(_Row):
        def save(self, **k):
            events["saved"].append(self.get("status"))

        def reload(self):
            pass

    def get_value(doctype, name, field, for_update=False, **k):
        events["get_value"].append((doctype, name, field, for_update))
        return status

    frappe.db = types.SimpleNamespace(get_value=get_value, commit=lambda: None,
                                      set_value=lambda *a, **k: None)
    frappe.get_doc = lambda doctype, name: _Doc(name=name, status=status, started_at=None)
    frappe.get_single = lambda doctype: _Row(dbt_project_path="/zz")
    frappe.utils = types.SimpleNamespace(now_datetime=lambda: None)
    frappe.logger = lambda *a, **k: types.SimpleNamespace(info=lambda *a: None,
                                                          warning=lambda *a: None)

    def popen(*a, **k):
        events["popen"].append(a)
        raise RuntimeError("zz: no dbt here")

    import contextlib
    ns = {
        "frappe": frappe,
        "subprocess": types.SimpleNamespace(Popen=popen, PIPE=-1, STDOUT=-2,
                                            TimeoutExpired=TimeoutError),
        "writing": lambda *a: contextlib.nullcontext(),
        "WORKER_WRITER": "worker",
        "_emit": lambda *a, **k: None,
        "_dbt_bin": lambda: "dbt",
        "_parse_results": lambda doc, path: None,
    }
    fn = _function(ASSERTION_RUN, "run_close_assertions")
    exec(compile(ast.Module(body=[fn], type_ignores=[]), ASSERTION_RUN, "exec"), ns)
    return ns["run_close_assertions"], events


def test_the_close_assertions_job_quits_unless_its_run_is_still_queued():
    """A run cancelled, failed by the reaper, or already started by another
    copy of the job must not be set Running and run dbt test again."""
    for status in ("Cancelled", "Failed", "Running", "Green", None):
        job, events = _run_close_assertions_ns(status)
        job("ZZ-AR-9")
        assert events["popen"] == [] and events["saved"] == [], (status, events)
        assert events["get_value"] and events["get_value"][0][3] is True, \
            "the status must be read with a locking read (FOR UPDATE)"


def test_the_close_assertions_job_runs_a_queued_run():
    job, events = _run_close_assertions_ns("Queued")
    job("ZZ-AR-9")
    assert events["popen"], events
    assert events["saved"] and events["saved"][0] == "Running", events


# 4. A Transforming run whose job is gone can be cancelled by an EPM Admin.


def _dead_job_site(alive, build_approval="ZZ-BAPR-7", rq_error=None):
    site = _Site(pipeline_runs=[{"name": "ZZ-PR-8", "status": "Transforming",
                                 "build_approval": build_approval, "error_log": "earlier"}])
    site.rows["Build Approval"]["ZZ-BAPR-7"] = {"name": "ZZ-BAPR-7", "workflow_state": "Running"}
    site.asked = []

    def is_job_enqueued(job_id):
        site.asked.append(job_id)
        if rq_error:
            raise rq_error
        return alive

    bj = types.ModuleType("frappe.utils.background_jobs")
    bj.is_job_enqueued = is_job_enqueued
    utils = types.ModuleType("frappe.utils")
    utils.background_jobs = bj
    utils.now_datetime = lambda: "2026-11-06 10:00:00"
    site.frappe.utils = utils
    site.frappe.session = types.SimpleNamespace(user="zz-admin@example.com")
    site.set_values = []

    def set_value(doctype, name, values, *a, **k):
        site.set_values.append((doctype, name, values))
        site.rows[doctype][name].update(values)
    site.frappe.db.set_value = set_value
    site.frappe.db.get_value = lambda doctype, name, field, **k: site.rows[doctype][name].get(field)

    ba = types.ModuleType("konsol.pipeline.doctype.build_approval.build_approval")
    ba.governed_build_job_id = lambda name: f"konsol-governed-build::{name}"
    pr = types.ModuleType("konsol.pipeline.doctype.pipeline_run.pipeline_run")
    pr.pipeline_run_job_id = lambda name: f"konsol-pipeline-run::{name}"
    site.mods.update({"frappe.utils": utils, "frappe.utils.background_jobs": bj,
                      "konsol.pipeline.doctype.build_approval.build_approval": ba,
                      "konsol.pipeline.doctype.pipeline_run.pipeline_run": pr})
    site.install()
    return site


def test_a_transforming_run_whose_job_is_gone_can_be_cancelled_and_says_why():
    site = _dead_job_site(alive=False)
    assert _api().cancel_run("ZZ-PR-8") == "ZZ-PR-8"
    run = site.rows["Pipeline Run"]["ZZ-PR-8"]
    assert run["status"] == "Cancelled", run
    assert site.asked == ["konsol-governed-build::ZZ-BAPR-7"], site.asked
    log = run.get("error_log") or ""
    assert "earlier" in log, "the earlier log was overwritten"
    for part in ("konsol-governed-build::ZZ-BAPR-7", "no longer", "zz-admin@example.com"):
        assert part in log, (part, log)
    # its Build Approval stops counting as in flight too, saying why
    ba = site.rows["Build Approval"]["ZZ-BAPR-7"]
    assert ba["workflow_state"] == "Failed", ba
    assert "ZZ-PR-8" in (ba.get("error_message") or ""), ba


def test_a_transforming_run_whose_job_is_alive_is_still_refused():
    site = _dead_job_site(alive=True)
    msg = _refused(lambda: _api().cancel_run("ZZ-PR-8"))
    assert "ZZ-PR-8" in msg, msg
    assert site.rows["Pipeline Run"]["ZZ-PR-8"]["status"] == "Transforming"


def test_a_transforming_run_is_refused_when_rq_cannot_be_asked():
    site = _dead_job_site(alive=False, rq_error=ConnectionError("redis down"))
    _refused(lambda: _api().cancel_run("ZZ-PR-8"))
    assert site.rows["Pipeline Run"]["ZZ-PR-8"]["status"] == "Transforming"


def test_a_legacy_transforming_run_is_looked_up_by_its_own_job_id():
    site = _dead_job_site(alive=False, build_approval=None)
    assert _api().cancel_run("ZZ-PR-8") == "ZZ-PR-8"
    assert site.asked == ["konsol-pipeline-run::ZZ-PR-8"], site.asked


def test_the_legacy_pipeline_job_is_enqueued_under_that_job_id():
    path = os.path.join(APP_DIR, "pipeline", "doctype", "pipeline_run", "pipeline_run.py")
    body = ast.unparse(_function(path, "trigger_pipeline"))
    assert "job_id=pipeline_run_job_id(doc.name)" in body, body

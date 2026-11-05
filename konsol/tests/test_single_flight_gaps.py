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
                site.rows["Pipeline Run"][self["name"]]["status"] = self["status"]

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
        self.mods = {"frappe": frappe, "konsol.schema_lifecycle": lifecycle}

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

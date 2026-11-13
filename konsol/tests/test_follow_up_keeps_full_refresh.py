"""A full refresh asked for while a build runs reaches the follow-up (#261).

A Dimension publish or unpublish asks for a full-scope build with
--full-refresh (schema_lifecycle.apply_and_rebuild). If a full-scope build is
already Running, the debounce absorbs the request and flags the Running row for
one more build (#129). That build's dbt command is already built, so raising
full_refresh on the Running row does nothing; the follow-up that its finish
requests (tasks._finish_governed_build -> request_build_for_scope) carried no
full_refresh, so the schema change never got its full refresh. Now the Running
row holds the pending full refresh (rebuild_full_refresh) and the follow-up,
or the pending build that absorbs it, carries it. Exactly once.

frappe is stubbed: one transaction, Build Approval rows in a dict. The live
A/B is in the PR."""
import importlib.util
import os
import re
import sys
import types

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class _D(dict):
    """frappe._dict: attribute reads and writes are item reads and writes."""
    __getattr__ = dict.get
    __setattr__ = dict.__setitem__


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Site:
    """Build Approval rows, and the frappe calls the request and finish paths make."""

    def __init__(self, **rows):
        self.rows = {name: _D(dict(dict(full_refresh=0, rebuild_requested=0, rebuild_full_refresh=0,
                                        build_scope="full"), **row), name=name)
                     for name, row in rows.items()}
        self.created = []

    def _new_row(self, fields):
        name = f"BA-N{len(self.created) + 1}"
        self.rows[name] = _D(dict(dict(rebuild_full_refresh=0, rebuild_requested=0, full_refresh=0),
                                  **fields, name=name, workflow_state="Pending Review"))
        self.created.append(name)
        return name

    # --- frappe.db ---------------------------------------------------------
    def sql(self, query, params=None, as_dict=False, **k):
        q = " ".join(query.split())
        if "FROM `tabDocType`" in q:
            return []
        if q.startswith("SELECT name, workflow_state FROM `tabBuild Approval`"):
            scope = params["scope"] if isinstance(params, dict) else params
            states = (params["states"] if isinstance(params, dict)
                      else ("Draft", "Pending Review", "Approved", "Running"))
            return [_D(name=r.name, workflow_state=r.workflow_state) for r in self.rows.values()
                    if r.build_scope == scope and r.workflow_state in states][:1]
        m = re.match(r"SELECT (.+?) FROM `tabBuild Approval` WHERE name = %s FOR UPDATE", q)
        if m:
            row = self.rows[params]
            return ((*(row[c.strip()] for c in m.group(1).split(",")),),)
        m = re.match(r"UPDATE `tabBuild Approval` SET (\w+) = 1 WHERE name = %s", q)
        if m:
            self.rows[params][m.group(1)] = 1
            return ()
        raise AssertionError(f"unexpected SQL: {q}")

    def get_value(self, doctype, name, field, **k):
        return self.rows[name][field]

    def set_value(self, doctype, name, field, value, **k):
        self.rows[name][field] = value

    # --- frappe ------------------------------------------------------------
    def frappe(self):
        site = self
        frappe = types.ModuleType("frappe")
        frappe.db = types.SimpleNamespace(sql=self.sql, get_value=self.get_value, set_value=self.set_value,
                                          commit=lambda: None, rollback=lambda: None)
        frappe.flags = _D()
        frappe.session = types.SimpleNamespace(user="Administrator")
        frappe.local = types.SimpleNamespace(session=_D(user="Administrator", sid="x", data={}), form_dict={})
        frappe.set_user = lambda user: None
        frappe.msgprint = lambda *a, **k: None
        frappe.publish_realtime = lambda *a, **k: None
        frappe.log_error = lambda *a, **k: (_ for _ in ()).throw(AssertionError(f"log_error: {a} {k}"))
        frappe.logger = lambda: types.SimpleNamespace(info=lambda *a: None, warning=lambda *a: None)
        frappe.utils = types.SimpleNamespace(now_datetime=lambda: None)

        class NewDoc(_D):
            def insert(self, **k):
                self.name = site._new_row({k: v for k, v in self.items() if k != "doctype"})

        frappe.new_doc = lambda doctype: NewDoc(doctype=doctype)
        return frappe

    def modules(self):
        """Load schema_lifecycle and tasks against this site's frappe."""
        frappe = self.frappe()
        stubs = {
            "frappe": frappe,
            "konsol.airbyte_service": types.SimpleNamespace(AirbyteClient=object),
            "konsol.build_command": types.SimpleNamespace(dbt_build_command=None),
            "konsol.orchestrator.reaper": types.SimpleNamespace(START_FAILURE_PREFIX="Governed build could not start"),
            "konsol.close.fingerprint": types.SimpleNamespace(check_after_build=lambda *a: []),
            "konsol.pipeline.doctype.build_approval.build_approval": types.SimpleNamespace(
                AUTO_APPROVE_FLAG="konsol_auto_approve"),
        }
        saved = {m: sys.modules.get(m) for m in list(stubs) + ["konsol.build_lock"]}
        sys.modules.update(stubs)
        sys.modules["konsol.build_lock"] = _load("build_lock_fr", os.path.join(APP_DIR, "build_lock.py"))
        lifecycle = _load("schema_lifecycle_fr", os.path.join(APP_DIR, "schema_lifecycle.py"))
        tasks = _load("tasks_fr", os.path.join(APP_DIR, "tasks.py"))
        self._saved = saved
        return lifecycle, tasks

    def restore(self):
        for m, old in self._saved.items():
            if old is None:
                sys.modules.pop(m, None)
            else:
                sys.modules[m] = old

    # --- the scenario's steps ---------------------------------------------
    def publish(self, lifecycle, full_refresh=True):
        """A Dimension publish's build request (apply_and_rebuild, after its DDL)."""
        dim = types.SimpleNamespace(doctype="Dimension", name="dim_zz")
        return lifecycle._request_governed_build(dim, "Publish", full_refresh=full_refresh)

    def finish(self, tasks, name, state="Completed"):
        """The Running build's job ends: its doc was loaded at the start."""
        site = self

        class Doc(_D):
            def save(self, **k):
                site.rows[self.name].update(self)

        doc = Doc(self.rows[name], doctype="Build Approval", workflow_state=state,
                  started_at=None, completed_at=None)
        tasks._finish_governed_build(doc)


def _run(site, steps):
    lifecycle, tasks = site.modules()
    try:
        steps(lifecycle, tasks)
    finally:
        site.restore()


def test_a_full_refresh_asked_while_running_is_carried_by_the_follow_up():
    site = Site(**{"BA-1": dict(workflow_state="Running")})

    def steps(lifecycle, tasks):
        assert site.publish(lifecycle) == "BA-1", "absorbed by the Running build"
        site.finish(tasks, "BA-1")
    _run(site, steps)
    assert len(site.created) == 1, site.created
    follow_up = site.rows[site.created[0]]
    assert follow_up.full_refresh == 1, (
        "the follow-up of a build that absorbed a full-refresh request must run with --full-refresh")
    assert follow_up.rebuild_requested == 1, "and is flagged as carrying changes (#140)"


def test_two_full_refresh_requests_while_running_make_one_follow_up():
    site = Site(**{"BA-1": dict(workflow_state="Running")})

    def steps(lifecycle, tasks):
        site.publish(lifecycle)
        site.publish(lifecycle)
        site.finish(tasks, "BA-1")
    _run(site, steps)
    assert len(site.created) == 1 and site.rows[site.created[0]].full_refresh == 1


def test_the_running_row_records_what_it_ran():
    """Its dbt command was built without --full-refresh: full_refresh on the
    row stays 0, so the row tells the truth about its own run."""
    site = Site(**{"BA-1": dict(workflow_state="Running")})
    _run(site, lambda lifecycle, tasks: site.publish(lifecycle))
    assert site.rows["BA-1"].full_refresh == 0
    assert site.rows["BA-1"].rebuild_full_refresh == 1 and site.rows["BA-1"].rebuild_requested == 1


def test_a_plain_request_while_running_gets_a_plain_follow_up():
    site = Site(**{"BA-1": dict(workflow_state="Running")})

    def steps(lifecycle, tasks):
        site.publish(lifecycle, full_refresh=False)
        site.finish(tasks, "BA-1")
    _run(site, steps)
    assert len(site.created) == 1 and site.rows[site.created[0]].full_refresh == 0


def test_a_full_refresh_follow_up_absorbed_by_a_pending_build_raises_its_flag():
    """A request committed between the finish and its follow-up made a new
    pending approval; the follow-up is absorbed into it, which must then run
    with --full-refresh."""
    site = Site(**{"BA-1": dict(workflow_state="Running"), "BA-2": dict(workflow_state="Completed")})

    def steps(lifecycle, tasks):
        site.publish(lifecycle)
        site.rows["BA-2"].update(workflow_state="Approved")   # the request that slipped in
        site.finish(tasks, "BA-1")
    _run(site, steps)
    assert site.created == [] and site.rows["BA-2"].full_refresh == 1


def test_a_full_refresh_request_while_pending_still_raises_the_pending_row():
    """Unchanged (#261): a build that has not started reads full_refresh at its start."""
    site = Site(**{"BA-1": dict(workflow_state="Approved")})
    _run(site, lambda lifecycle, tasks: site.publish(lifecycle))
    assert site.rows["BA-1"].full_refresh == 1 and site.created == []


# --- the reaper's follow-ups (a build that died, or never started) ----------

def _reaper():
    return _load("reaper_fr", os.path.join(APP_DIR, "orchestrator", "reaper.py"))


def test_a_reaped_running_build_passes_its_pending_full_refresh_on():
    decide = _reaper().follow_up_full_refresh
    ran = dict(workflow_state="Failed", started_at="2026-11-13 10:00")
    assert decide(dict(ran, rebuild_full_refresh=1, full_refresh=0)) is True
    assert decide(dict(ran, rebuild_full_refresh=0, full_refresh=1)) is False, "it ran its own refresh"
    never = dict(workflow_state="Failed", started_at=None)
    assert decide(dict(never, rebuild_full_refresh=0, full_refresh=1)) is True, "never ran it"
    assert decide(dict(never, rebuild_full_refresh=0, full_refresh=0)) is False


def test_both_reaper_follow_ups_pass_full_refresh():
    with open(os.path.join(APP_DIR, "orchestrator", "reaper.py")) as f:
        src = f.read()
    reap = src.split("def reap_stale_build_approvals")[1].split("\ndef ")[0]
    assert "full_refresh=follow_up_full_refresh(as_reaped)" in reap
    sweep = src.split("def follow_up_failed_starts")[1].split("\ndef ")[0]
    assert "SELECT name, full_refresh, rebuild_full_refresh FROM `tabBuild Approval`" in sweep
    assert "full_refresh=full_refresh)" in sweep


# --- the field, and the controller's hold on it -----------------------------

def test_the_pending_full_refresh_is_a_read_only_check():
    import json
    with open(os.path.join(APP_DIR, "pipeline", "doctype", "build_approval", "build_approval.json")) as f:
        field = next(f for f in json.load(f)["fields"] if f["fieldname"] == "rebuild_full_refresh")
    assert field["fieldtype"] == "Check" and field["read_only"] == 1 and field["default"] == "0"


def test_a_save_keeps_the_pending_full_refresh_and_a_rerun_spends_it():
    with open(os.path.join(APP_DIR, "pipeline", "doctype", "build_approval", "build_approval.py")) as f:
        src = f.read()
    assert 'if before and getattr(before, "rebuild_full_refresh", 0) and not rerun:' in src
    rerun = src.split("if rerun:")[1].split("# The old error goes")[0]
    assert "self.rebuild_requested = 0" in rerun and "self.rebuild_full_refresh = 0" in rerun

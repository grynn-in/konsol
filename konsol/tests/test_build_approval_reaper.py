"""A stuck Build Approval blocks every auto-build for its scope (#125).

The staleness rule is pure and tested here; the sweep is proved live."""
import os
import re
from datetime import datetime, timedelta

import importlib.util

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Loaded by path: importing the konsol package needs frappe, and the host
# runner skips any test file that can't import (as test_orchestrator_reaper.py is).
_spec = importlib.util.spec_from_file_location("reaper", os.path.join(APP_DIR, "orchestrator", "reaper.py"))
reaper = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(reaper)
NOW = datetime(2026, 9, 12, 12, 0, 0)
LONG_AGO = NOW - timedelta(minutes=reaper.STALE_BUILD_APPROVAL_MINUTES + 1)
JUST_NOW = NOW - timedelta(minutes=1)


def reason(**row):
    return reaper.stale_build_approval_reason(row, NOW)


def test_an_approved_build_whose_job_was_lost_is_reaped():
    assert reason(workflow_state="Approved", modified=LONG_AGO, started_at=None)


def test_a_freshly_approved_build_is_left_alone():
    assert reason(workflow_state="Approved", modified=JUST_NOW, started_at=None) is None


def test_an_approved_build_that_started_is_judged_as_running_not_lost():
    assert reason(workflow_state="Approved", modified=LONG_AGO, started_at=LONG_AGO) is None


def test_a_build_running_past_its_job_timeout_is_reaped():
    assert reason(workflow_state="Running", started_at=LONG_AGO, modified=JUST_NOW)


def test_a_running_build_within_its_timeout_is_left_alone():
    assert reason(workflow_state="Running", started_at=JUST_NOW, modified=LONG_AGO) is None


def test_other_states_are_never_reaped():
    for state in ("Draft", "Pending Review", "Completed", "Failed", "Cancelled"):
        assert reason(workflow_state=state, modified=LONG_AGO, started_at=LONG_AGO) is None


def test_missing_timestamps_are_never_reaped():
    assert reason(workflow_state="Approved", modified=None, started_at=None) is None
    assert reason(workflow_state="Running", modified=None, started_at=None) is None


def test_the_timeout_exceeds_the_build_job_timeout():
    """A live build must never be reaped: the window must exceed the RQ job
    timeout that bounds run_governed_build."""
    with open(os.path.join(APP_DIR, "pipeline", "doctype", "build_approval", "build_approval.py")) as f:
        job_timeout = int(re.search(r"run_governed_build.*?timeout=(\d+)", f.read(), re.S).group(1))
    assert reaper.STALE_BUILD_APPROVAL_MINUTES * 60 > 2 * job_timeout


def test_the_sweep_is_scheduled_and_never_overwrites_a_state_it_did_not_read():
    with open(os.path.join(APP_DIR, "hooks.py")) as f:
        assert "konsol.orchestrator.reaper.reap_stale_build_approvals" in f.read()
    with open(os.path.join(APP_DIR, "orchestrator", "reaper.py")) as f:
        body = f.read().split("def reap_stale_build_approvals")[1]
    assert "WHERE name = %s AND workflow_state = %s" in body


def test_a_reaped_approval_whose_job_turns_up_late_does_not_build():
    """The first statement after loading the doc returns unless it is Approved."""
    import ast
    with open(os.path.join(APP_DIR, "tasks.py")) as f:
        tree = ast.parse(f.read())
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "run_governed_build")
    guard = fn.body[fn.body.index(next(n for n in fn.body if isinstance(n, ast.If))) ]
    assert ast.unparse(guard.test) == "doc.workflow_state != 'Approved'"
    assert isinstance(guard.body[-1], ast.Return)
    before = fn.body[:fn.body.index(guard)]
    assert all("get_doc" in ast.unparse(n) or isinstance(n, ast.Expr) for n in before), "nothing may run before the guard"


def _sweep_sql():
    with open(os.path.join(APP_DIR, "orchestrator", "reaper.py")) as f:
        return f.read().split("def reap_stale_build_approvals")[1]


def test_the_sweep_bumps_modified_so_a_late_save_fails_its_timestamp_check():
    body = _sweep_sql()
    update = body[body.index("UPDATE `tabBuild Approval`"):body.index("WHERE name = %s")]
    assert "modified = %s" in update


def test_a_reaped_running_build_releases_its_pipeline_run():
    body = _sweep_sql()
    assert 'if row["workflow_state"] == "Running":' in body
    pr = body[body.index("UPDATE `tabPipeline Run`"):]
    assert "SET status = 'Failed'" in pr and "WHERE build_approval = %s AND status IN %s" in pr


def test_an_approved_build_still_waiting_in_rq_is_not_reaped():
    """One worker serves every queue, so an approved build can wait behind a
    30-minute pipeline run (#137 review). The sweep asks RQ by the job's id,
    which _enqueue_build now sets, and treats an RQ error as still waiting."""
    import ast
    body = _sweep_sql()
    assert 'row["workflow_state"] == "Approved" and _build_job_waiting(row["name"])' in body
    with open(os.path.join(APP_DIR, "orchestrator", "reaper.py")) as f:
        waiting = f.read().split("def _build_job_waiting")[1].split("\ndef ")[0]
    assert "is_job_enqueued(governed_build_job_id(name))" in waiting
    assert "except Exception:\n        return True" in waiting
    path = os.path.join(APP_DIR, "pipeline", "doctype", "build_approval", "build_approval.py")
    with open(path) as f:
        tree = ast.parse(f.read())
    fn = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "_enqueue_build")
    call = next(n for n in ast.walk(fn) if isinstance(n, ast.Call) and getattr(n.func, "attr", "") == "enqueue")
    kw = {k.arg: ast.unparse(k.value) for k in call.keywords}
    assert kw.get("job_id") == "governed_build_job_id(self.name)"
    assert kw.get("deduplicate") == "True", "a reused job id without it can drop a re-approval's build"


def test_nothing_else_happens_when_the_reap_update_matched_no_row():
    body = _sweep_sql()
    update_end = body.index("WHERE name = %s AND workflow_state = %s")
    check = body.index('!= note:\n            continue', update_end)
    assert check < body.index("UPDATE `tabPipeline Run`") and check < body.index("reaped.append")


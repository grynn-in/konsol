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
    with open(os.path.join(APP_DIR, "tasks.py")) as f:
        body = f.read().split("def run_governed_build")[1].split("\ndef ")[0]
    guard = body.index('if doc.workflow_state != "Approved":')
    assert guard < body.index("single_flight_lock()") and "return" in body[guard:guard + 300]


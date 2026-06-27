"""PRD-15 — resume-from-step + retry planning core.

Host tests for the pure :mod:`konsol.orchestrator.resume` module: ``plan_resume``
and ``plan_retry`` compute the reset snapshot for restarting a settled run from a
chosen step. Frappe-bound API wrappers are guarded with ``importorskip``.
"""
import inspect
import os

import pytest

from konsol.orchestrator import resume
from konsol.orchestrator.dag import Dag
from konsol.orchestrator.plan import DEFAULT_DEFINITION
from konsol.orchestrator.state import RunState, Status


ORCH_DIR = os.path.dirname(resume.__file__)


def _all(status):
    return {s.id: status for s in DEFAULT_DEFINITION}


def _runnable_ids(steps, snapshot):
    state = RunState(Dag(steps), snapshot)
    return [s.id for s in state.runnable()]


# ---- purity / no top-level frappe ------------------------------------------

def test_resume_module_has_no_toplevel_frappe_import():
    with open(os.path.join(ORCH_DIR, "resume.py")) as fh:
        src = fh.read()
    for line in src.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        # top-level (column 0) import must not be frappe
        if line.startswith("import frappe") or line.startswith("from frappe"):
            raise AssertionError("resume.py must not import frappe at top level")


def test_plan_functions_exist():
    assert callable(resume.plan_resume)
    assert callable(resume.plan_retry)


# ---- plan_resume -----------------------------------------------------------

def test_plan_resume_resets_step_and_descendants():
    snap = resume.plan_resume(DEFAULT_DEFINITION, _all(Status.SUCCESS), "silver")
    # upstream preserved
    assert snap["extract"] == Status.SUCCESS
    assert snap["seed"] == Status.SUCCESS
    # target + descendants reset
    assert snap["silver"] == Status.PENDING
    assert snap["gold"] == Status.PENDING
    assert snap["assertions"] == Status.PENDING
    assert snap["signoff"] == Status.PENDING


def test_plan_resume_runnable_is_target_only():
    snap = resume.plan_resume(DEFAULT_DEFINITION, _all(Status.SUCCESS), "silver")
    assert _runnable_ids(DEFAULT_DEFINITION, snap) == ["silver"]


def test_plan_resume_from_root_resets_all():
    snap = resume.plan_resume(DEFAULT_DEFINITION, _all(Status.SUCCESS), "extract")
    assert all(v == Status.PENDING for v in snap.values())
    assert _runnable_ids(DEFAULT_DEFINITION, snap) == ["extract"]


def test_plan_resume_unknown_step_raises():
    with pytest.raises(ValueError):
        resume.plan_resume(DEFAULT_DEFINITION, _all(Status.SUCCESS), "nope")


def test_plan_resume_unsettled_run_raises():
    statuses = _all(Status.SUCCESS)
    statuses["gold"] = Status.RUNNING
    with pytest.raises(ValueError):
        resume.plan_resume(DEFAULT_DEFINITION, statuses, "silver")


def test_plan_resume_fresh_run_is_unsettled():
    # a not-yet-started run is runnable (extract) → not settled → cannot resume
    with pytest.raises(ValueError):
        resume.plan_resume(DEFAULT_DEFINITION, _all(Status.PENDING), "silver")


# ---- plan_retry ------------------------------------------------------------

def test_plan_retry_resets_failed_step_and_descendants():
    statuses = {
        "extract": Status.SUCCESS,
        "seed": Status.SUCCESS,
        "silver": Status.SUCCESS,
        "gold": Status.FAILED,
        "assertions": Status.PENDING,
        "signoff": Status.PENDING,
    }
    snap = resume.plan_retry(DEFAULT_DEFINITION, statuses, "gold")
    assert snap["silver"] == Status.SUCCESS
    assert snap["gold"] == Status.PENDING
    assert snap["assertions"] == Status.PENDING
    assert snap["signoff"] == Status.PENDING
    assert _runnable_ids(DEFAULT_DEFINITION, snap) == ["gold"]


def test_plan_retry_unknown_step_raises():
    statuses = {
        "extract": Status.SUCCESS,
        "seed": Status.SUCCESS,
        "silver": Status.SUCCESS,
        "gold": Status.FAILED,
        "assertions": Status.PENDING,
        "signoff": Status.PENDING,
    }
    with pytest.raises(ValueError):
        resume.plan_retry(DEFAULT_DEFINITION, statuses, "nope")


def test_plan_retry_unsettled_run_raises():
    statuses = _all(Status.SUCCESS)
    statuses["gold"] = Status.RUNNING
    with pytest.raises(ValueError):
        resume.plan_retry(DEFAULT_DEFINITION, statuses, "gold")


def test_plan_retry_does_not_mutate_input():
    statuses = {
        "extract": Status.SUCCESS,
        "seed": Status.SUCCESS,
        "silver": Status.SUCCESS,
        "gold": Status.FAILED,
        "assertions": Status.PENDING,
        "signoff": Status.PENDING,
    }
    before = dict(statuses)
    resume.plan_retry(DEFAULT_DEFINITION, statuses, "gold")
    assert statuses == before


# ---- frappe-bound API wrappers (guarded) -----------------------------------

def test_api_wrappers_exist_and_signatures():
    frappe = pytest.importorskip("frappe")  # noqa: F841
    from konsol.orchestrator import api

    assert callable(api.resume_run)
    assert callable(api.retry_step)
    assert list(inspect.signature(api.resume_run).parameters) == ["run_name", "step_id"]
    assert list(inspect.signature(api.retry_step).parameters) == ["run_name", "step_id"]

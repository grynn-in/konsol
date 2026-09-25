"""TDD — orchestrator exec-plane backend ``get_run`` (E4).

A whitelisted ``konsol.orchestrator.api.get_run(run_name)`` returns
``{name, status, steps:[...]}`` (the Pipeline Run child rows). Like the rest of
the orchestrator core it imports on the host without frappe; the behavioural
test is frappe-guarded with ``pytest.importorskip``.

konsol#305 R01 removed the konsol-exec SPA, and with it the static checks on
its ``src/api.js`` client that used to live here.
"""
import inspect

import pytest

from konsol.orchestrator import api


# ---- backend get_run surface (imports without frappe) -------------------

def test_get_run_is_callable():
    assert callable(api.get_run)


def test_get_run_name_preserved():
    assert api.get_run.__name__ == "get_run"


def test_get_run_signature():
    sig = inspect.signature(api.get_run)
    assert "run_name" in sig.parameters


# ---- backend launch_options surface (imports without frappe) ------------

def test_launch_options_is_callable():
    assert callable(api.launch_options)
    assert api.launch_options.__name__ == "launch_options"


# ---- backend get_run behaviour (frappe-guarded) -------------------------

def test_get_run_returns_run_with_steps():
    frappe = pytest.importorskip("frappe")  # noqa: F841

    class FakeRow:
        def __init__(self, **kw):
            self.__dict__.update(kw)

    class FakeRun:
        name = "PR-0001"
        status = "Running"
        steps = [
            FakeRow(
                step_id="silver",
                step_type="dbt",
                status="Success",
                started_at="2026-06-28 00:00:00",
                ended_at="2026-06-28 00:01:00",
                rows=42,
                output="ok",
                error="",
            )
        ]

    orig = frappe.get_doc
    frappe.get_doc = lambda *a, **k: FakeRun()
    try:
        out = api.get_run("PR-0001")
    finally:
        frappe.get_doc = orig

    assert out["name"] == "PR-0001"
    assert out["status"] == "Running"
    assert len(out["steps"]) == 1
    step = out["steps"][0]
    for field in (
        "step_id",
        "step_type",
        "status",
        "started_at",
        "ended_at",
        "rows",
        "output",
        "error",
    ):
        assert field in step, field
    assert step["step_id"] == "silver"
    assert step["rows"] == 42

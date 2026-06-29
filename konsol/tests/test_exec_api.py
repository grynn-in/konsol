"""TDD — konsol-exec exec-plane API client + backend ``get_run`` (E4).

E4 wires the konsol-exec Vite SPA to the PRD-10 orchestrator API:

1. **Backend** — a whitelisted ``konsol.orchestrator.api.get_run(run_name)``
   returning ``{name, status, steps:[...]}`` (the Pipeline Run child rows the
   SPA normalises via ``runModel.normalizeRun``). Like the rest of the
   orchestrator core it imports on the host without frappe; the behavioural
   test is frappe-guarded with ``pytest.importorskip``.
2. **SPA api client** — ``konsol-exec/src/api.js`` gains ``startRun`` /
   ``getRun`` / ``retryStep`` / ``resumeRun`` / ``cancelRun`` (each delegating
   to ``frappeCall("konsol.orchestrator.api.<fn>", ...)``) plus ``onRunStep``
   wrapping ``frappe.realtime?.on("orchestrator_step", cb)``.

Following the established static-assertion style, the SPA half reads the JS
source and asserts the functions + backend method strings + realtime topic are
present. No bench / no browser needed for the host suite.
"""
import inspect
import os

import pytest

from konsol.orchestrator import api

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
API_JS_PATH = os.path.join(
    os.path.dirname(APP_DIR), "konsol-exec", "src", "api.js"
)


def _api_js():
    with open(API_JS_PATH) as f:
        return f.read()


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


# ---- SPA api client (static assertion over api.js) ----------------------

def test_api_js_exists():
    assert os.path.exists(API_JS_PATH)


def test_api_js_exports_startRun():
    js = _api_js()
    assert "export function startRun" in js
    assert "konsol.orchestrator.api.start_run" in js


def test_api_js_exports_getRun():
    js = _api_js()
    assert "export function getRun" in js
    assert "konsol.orchestrator.api.get_run" in js


def test_api_js_exports_retryStep():
    js = _api_js()
    assert "export function retryStep" in js
    assert "konsol.orchestrator.api.retry_step" in js


def test_api_js_exports_resumeRun():
    js = _api_js()
    assert "export function resumeRun" in js
    assert "konsol.orchestrator.api.resume_run" in js


def test_api_js_exports_cancelRun():
    js = _api_js()
    assert "export function cancelRun" in js
    assert "konsol.orchestrator.api.cancel_run" in js


def test_api_js_exports_onRunStep_realtime():
    js = _api_js()
    assert "export function onRunStep" in js
    assert "orchestrator_step" in js
    assert "frappe.realtime" in js


def test_api_js_passes_step_id_arg():
    # retry/resume must forward the chosen step id to the backend
    js = _api_js()
    assert "step_id" in js


def test_api_js_exports_getLaunchOptions():
    js = _api_js()
    assert "export function getLaunchOptions" in js
    assert "konsol.orchestrator.api.launch_options" in js

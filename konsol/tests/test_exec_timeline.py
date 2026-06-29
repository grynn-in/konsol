"""TDD — konsol-exec live step timeline (E7).

E7 builds the Press/Airbyte-style timeline component for the konsol-exec
execution plane. ``RunTimeline`` renders a normalized run view-model (the
``normalizeRun``-shaped object held in the ``runExecMachine`` context): a run
header (run name + a status pill via ``statusTone``), a progress indicator
(``progressPct``), and one card per step (id, type, a ``statusTone`` pill,
started→ended, rows, output/error). Per-step **Retry**/**Resume** dispatch
``RETRY_STEP``/``RESUME_FROM`` and a run-level **Cancel** dispatches ``CANCEL``;
realtime ``orchestrator_step`` events (via ``onRunStep``) drive a refresh
(``RUN_STEP``/``REFRESH``).

Following the repo's static-assertion convention (JSX isn't host-runnable),
these tests read the component source and assert the required imports, the
per-step rendering, the status pill, and the dispatched events are present. All
data shaping lives in the pure ESM core; this component just wires it up.
"""
import os

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC_DIR = os.path.join(os.path.dirname(APP_DIR), "konsol-exec", "src")
TIMELINE_PATH = os.path.join(SRC_DIR, "components", "RunTimeline.jsx")


def _timeline_js():
    with open(TIMELINE_PATH) as f:
        return f.read()


# ---- file + export surface ---------------------------------------------

def test_timeline_file_exists():
    assert os.path.exists(TIMELINE_PATH)


def test_timeline_exported_named():
    js = _timeline_js()
    assert "export function RunTimeline" in js


def test_accepts_run_send_accent_props():
    js = _timeline_js()
    assert "run" in js
    assert "send" in js
    assert "accent" in js


# ---- imports react + the pure core + api -------------------------------

def test_imports_react():
    js = _timeline_js()
    assert "react" in js


def test_imports_status_tone():
    js = _timeline_js()
    assert "statusTone" in js
    assert "../orchestrator/status" in js


def test_imports_run_model():
    js = _timeline_js()
    assert "normalizeRun" in js
    assert "progressPct" in js
    assert "../orchestrator/runModel" in js


def test_imports_on_run_step():
    js = _timeline_js()
    assert "onRunStep" in js
    assert "../api" in js


# ---- run header + progress ---------------------------------------------

def test_renders_run_name_header():
    js = _timeline_js()
    assert "run.name" in js


def test_renders_status_pill():
    js = _timeline_js()
    assert "statusTone(" in js


def test_renders_progress():
    js = _timeline_js()
    assert "progressPct(" in js


# ---- per-step rendering ------------------------------------------------

def test_maps_over_steps():
    js = _timeline_js()
    assert ".steps" in js
    assert ".map(" in js


def test_renders_step_id_and_type():
    js = _timeline_js()
    assert "step.id" in js
    assert "step.type" in js


def test_renders_step_timestamps():
    js = _timeline_js()
    assert "step.startedAt" in js
    assert "step.endedAt" in js


def test_renders_step_rows():
    js = _timeline_js()
    assert "step.rows" in js


def test_renders_step_output_and_error():
    js = _timeline_js()
    assert "step.output" in js
    assert "step.error" in js


# ---- controls dispatch the machine events ------------------------------

def test_dispatches_retry_step():
    js = _timeline_js()
    assert "RETRY_STEP" in js


def test_dispatches_resume_from():
    js = _timeline_js()
    assert "RESUME_FROM" in js


def test_dispatches_cancel():
    js = _timeline_js()
    assert "CANCEL" in js


def test_passes_step_id_on_retry_resume():
    js = _timeline_js()
    assert "stepId" in js


# ---- realtime refresh --------------------------------------------------

def test_wires_realtime_refresh():
    js = _timeline_js()
    assert "useEffect" in js
    assert "onRunStep" in js
    # realtime event should drive a refresh of the run
    assert ("RUN_STEP" in js) or ("REFRESH" in js)

"""konsol-exec live step timeline (E7).

``RunTimeline`` renders a normalized run view-model (the ``normalizeRun``-shaped
object held in the ``runExecMachine`` context): a run header (run name + a
status badge via ``statusTone``), a progress indicator (``progressPct``), and
one row per step (id, type, status badge, started→ended, rows, output/error).
Per-step **Retry**/**Resume** dispatch ``RETRY_STEP``/``RESUME_FROM`` and a
run-level **Cancel** dispatches ``CANCEL``; realtime ``orchestrator_step``
events (via ``onRunStep``) drive a ``RUN_STEP`` refresh — that subscription is
what makes this a live monitor rather than a snapshot.

Ported from JSX to Vue SFC. Following the repo's static-assertion convention
(component source isn't host-runnable), these tests read the source and assert
the required imports, the per-step rendering, the status badge, and the
dispatched events are present. All data shaping lives in the pure ESM core;
this component just wires it up.
"""
import os

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC_DIR = os.path.join(os.path.dirname(APP_DIR), "konsol-exec", "src")
TIMELINE_PATH = os.path.join(SRC_DIR, "components", "RunTimeline.vue")


def _timeline_js():
    with open(TIMELINE_PATH) as f:
        return f.read()


# ---- file + component surface ------------------------------------------

def test_timeline_file_exists():
    assert os.path.exists(TIMELINE_PATH)


def test_timeline_is_a_vue_sfc():
    js = _timeline_js()
    assert "<script setup>" in js
    assert "<template>" in js


def test_accepts_run_prop_and_emits_send():
    js = _timeline_js()
    assert "defineProps" in js
    assert "run:" in js
    assert "defineEmits" in js
    assert '"send"' in js


# ---- imports the pure core + api ---------------------------------------

def test_imports_vue():
    js = _timeline_js()
    assert 'from "vue"' in js


def test_imports_status_tone():
    js = _timeline_js()
    assert "statusTone" in js
    assert "orchestrator/status.js" in js


def test_imports_run_model():
    js = _timeline_js()
    assert "orderSteps" in js
    assert "progressPct" in js
    assert "orchestrator/runModel.js" in js


def test_imports_on_run_step():
    js = _timeline_js()
    assert "onRunStep" in js
    assert 'from "../api.js"' in js


# ---- run header + progress ---------------------------------------------

def test_renders_run_name_header():
    js = _timeline_js()
    assert "run.name" in js


def test_renders_status_badge():
    js = _timeline_js()
    assert "Badge" in js
    assert "statusTone(run.status)" in js


def test_renders_progress():
    js = _timeline_js()
    assert "Progress" in js
    assert "progressPct" in js


# ---- per-step rendering ------------------------------------------------

def test_maps_over_steps():
    js = _timeline_js()
    assert "v-for" in js
    assert "steps" in js


def test_renders_step_id_and_type():
    js = _timeline_js()
    assert "s.id" in js
    assert "s.type" in js


def test_renders_step_timestamps():
    js = _timeline_js()
    assert "s.startedAt" in js
    assert "s.endedAt" in js


def test_renders_step_rows():
    js = _timeline_js()
    assert "s.rows" in js


def test_renders_step_output_and_error():
    js = _timeline_js()
    assert "s.output" in js
    assert "s.error" in js


# ---- dispatched events -------------------------------------------------

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
    assert "stepId: s.id" in js


def test_wires_realtime_refresh():
    """The subscription, and its teardown — an un-cleaned listener leaks a
    refresh into every later run."""
    js = _timeline_js()
    assert "onRunStep(" in js
    assert "RUN_STEP" in js
    assert "onBeforeUnmount" in js

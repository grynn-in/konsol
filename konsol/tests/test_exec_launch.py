"""konsol-exec launch plane (E5/E6).

``StepExecute`` is the launch surface for one close process. It builds run args
through the pure ``buildRunArgs`` helper and dispatches ``LAUNCH`` into the
``runExecMachine``.

Ported from ``ExecuteLaunch.jsx``. Two assertions changed meaning rather than
just path, and deliberately so: **fiscal year and fiscal period are no longer
fields on this form.** They moved to the period spine in the page heading, so
the launch inherits the period being closed instead of asking for it again.
The tests now assert the args still carry them, which is the property that
actually matters to the backend.
"""
import os

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC_DIR = os.path.join(os.path.dirname(APP_DIR), "konsol-exec", "src")
LAUNCH_PATH = os.path.join(SRC_DIR, "components", "StepExecute.vue")
RANGE_PATH = os.path.join(SRC_DIR, "components", "RangePicker.vue")


def _launch_js():
    with open(LAUNCH_PATH) as f:
        return f.read()


# ---- file + component surface ------------------------------------------

def test_launch_file_exists():
    assert os.path.exists(LAUNCH_PATH)


def test_launch_is_a_vue_sfc():
    js = _launch_js()
    assert "<script setup>" in js
    assert "<template>" in js


def test_imports_vue():
    js = _launch_js()
    assert 'from "vue"' in js


def test_imports_build_run_args():
    js = _launch_js()
    assert "buildRunArgs" in js
    assert "orchestrator/params.js" in js


# ---- the run is scoped by period, scope, definition --------------------

def test_launch_args_carry_fiscal_year():
    """No longer a form field — inherited from the period spine."""
    js = _launch_js()
    assert "fiscal_year" in js
    assert "period.year" in js


def test_launch_args_carry_fiscal_period():
    js = _launch_js()
    assert "fiscal_period" in js
    assert "period.period" in js


def test_renders_scope_field():
    js = _launch_js()
    assert "scope" in js
    assert "scopeOptions" in js


def test_renders_definition_field():
    js = _launch_js()
    assert "definition" in js
    assert "definitionOptions" in js


def test_renders_full_refresh_toggle():
    js = _launch_js()
    assert "full_refresh" in js
    assert "Switch" in js


def test_no_per_run_skip_sync_control():
    """skip_sync is a pipeline-level concern, never a per-run checkbox."""
    js = _launch_js()
    assert "skip_sync" not in js


def test_scalar_fields_are_selects():
    js = _launch_js()
    assert "Select" in js


def test_fetches_launch_options():
    """Options come from the shared plane, fetched once, not per launch form."""
    js = _launch_js()
    assert "plane.options" in js


def test_options_have_default_blank_entry():
    js = _launch_js()
    assert 'value: ""' in js


# ---- dispatch ----------------------------------------------------------

def test_calls_build_run_args():
    js = _launch_js()
    assert "buildRunArgs({" in js


def test_dispatches_launch_event():
    js = _launch_js()
    assert '"LAUNCH"' in js


def test_uses_run_exec_machine():
    js = _launch_js()
    assert "runExecMachine" in js
    assert "useMachine" in js


# ---- the build range ---------------------------------------------------

def test_build_range_is_an_explicit_control():
    """It was click + shift-click on the progress rail, which nobody could
    find. It is now a labelled from/through picker."""
    assert os.path.exists(RANGE_PATH)
    js = _launch_js()
    assert "RangePicker" in js


def test_range_applied_through_tested_helper():
    js = _launch_js()
    assert "withStageRange" in js


def test_range_sends_stage_ids_not_labels():
    with open(RANGE_PATH) as f:
        rng = f.read()
    assert "describeStageRange" in rng
    js = _launch_js()
    assert "from_stage" not in js, "stage ids are applied by withStageRange, not inline"

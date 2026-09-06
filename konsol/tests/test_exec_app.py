"""konsol-exec shell + step wiring (E5).

The execution plane lives inside a close step: ``StepDetail`` registers the
Setup / Execute / Monitor / History tabs and renders ``StepExecute``, which
instantiates the ``runExecMachine`` and renders ``RunTimeline`` beneath the
launch form.

Ported from ``App.jsx``. The wiring moved down a level — App used to own both
the subview registration and the machine; navigation now belongs to vue-router
and the machine belongs to the step that uses it. These tests follow it there.
"""
import os

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC_DIR = os.path.join(os.path.dirname(APP_DIR), "konsol-exec", "src")
APP_PATH = os.path.join(SRC_DIR, "App.vue")
DETAIL_PATH = os.path.join(SRC_DIR, "components", "StepDetail.vue")
EXECUTE_PATH = os.path.join(SRC_DIR, "components", "StepExecute.vue")
CONSTANTS_PATH = os.path.join(SRC_DIR, "constants.js")
ROUTER_PATH = os.path.join(SRC_DIR, "router.js")


def _read(path):
    with open(path) as f:
        return f.read()


# ---- shell -------------------------------------------------------------

def test_app_file_exists():
    assert os.path.exists(APP_PATH)


def test_app_is_a_vue_sfc():
    js = _read(APP_PATH)
    assert "<script setup>" in js
    assert "<template>" in js


def test_app_owns_the_close_machine():
    js = _read(APP_PATH)
    assert "closeMachine" in js
    assert "useMachine" in js


def test_app_provides_the_plane_to_the_tree():
    """Every screen reads snapshot data, period and send from one provide."""
    js = _read(APP_PATH)
    assert 'provide("plane"' in js


def test_app_renders_router_view():
    js = _read(APP_PATH)
    assert "RouterView" in js


def test_app_has_loading_and_error_states():
    js = _read(APP_PATH)
    assert "AppSkeleton" in js
    assert "ErrorState" in js


# ---- step detail registers the tabs ------------------------------------

def test_step_detail_registers_execute_subview():
    js = _read(DETAIL_PATH)
    assert "StepExecute" in js


def test_step_detail_renders_all_four_tabs():
    js = _read(DETAIL_PATH)
    for comp in ("StepSetup", "StepExecute", "StepMonitor", "StepHistory"):
        assert comp in js, f"{comp} not wired into StepDetail"


def test_execute_subview_registered_in_constants():
    js = _read(CONSTANTS_PATH)
    assert "STEP_TABS" in js
    assert '"execute"' in js


def test_tab_is_addressable():
    """A tab has its own URL so it can be pasted during a close."""
    js = _read(ROUTER_PATH)
    assert ":step" in js
    assert ":tab" in js


# ---- the execute step wires the machine to the timeline ----------------

def test_execute_instantiates_run_exec_machine():
    js = _read(EXECUTE_PATH)
    assert "runExecMachine" in js
    assert "useMachine" in js


def test_execute_renders_run_timeline():
    js = _read(EXECUTE_PATH)
    assert "RunTimeline" in js


def test_execute_passes_run_and_send_to_timeline():
    js = _read(EXECUTE_PATH)
    assert "snapshot.context.run" in js
    assert '@send="send"' in js

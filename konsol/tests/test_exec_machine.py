"""TDD — konsol-exec exec state machine (E5).

E5 builds the XState machine that drives a full orchestrator run from the
konsol-exec Vite SPA: ``LAUNCH`` (invoke ``startRun`` → watch),
``REFRESH``/realtime (``getRun`` → ``normalizeRun``), and
``RETRY_STEP``/``RESUME_FROM``/``CANCEL`` step controls, settling when the run
status ``isTerminal``. All data-shaping stays in the pure ESM core
(``orchestrator/runModel.js`` + ``orchestrator/status.js``); the machine only
orchestrates effects + transitions.

Following the repo's static-assertion convention (the machine pulls XState
which isn't host-runnable), these tests read the JS source and assert the
required states/events/actors + the pure-core/api imports are present, plus
that the machine is exported from the ``machines/index.js`` barrel.
"""
import os

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC_DIR = os.path.join(os.path.dirname(APP_DIR), "konsol-exec", "src")
MACHINE_PATH = os.path.join(SRC_DIR, "machines", "runExecMachine.js")
INDEX_PATH = os.path.join(SRC_DIR, "machines", "index.js")


def _machine_js():
    with open(MACHINE_PATH) as f:
        return f.read()


def _index_js():
    with open(INDEX_PATH) as f:
        return f.read()


# ---- file + export surface ---------------------------------------------

def test_machine_file_exists():
    assert os.path.exists(MACHINE_PATH)


def test_machine_exported_named():
    js = _machine_js()
    assert "export const runExecMachine" in js


def test_watching_polls_as_realtime_safety_net():
    """The watching state must poll (after-delay → refreshing) so the timeline
    converges even when realtime `orchestrator_step` events don't reach the
    browser (the 'PIPE-xxxxx shows 0 steps' bug)."""
    js = _machine_js()
    assert "after:" in js


def test_machine_exported_from_index():
    idx = _index_js()
    assert "runExecMachine" in idx


# ---- imports the pure core + api client --------------------------------

def test_imports_api_client():
    js = _machine_js()
    assert "../api" in js
    for fn in ("startRun", "getRun", "retryStep", "resumeRun", "cancelRun"):
        assert fn in js, fn


def test_imports_pure_core():
    js = _machine_js()
    assert "normalizeRun" in js
    assert "../orchestrator/runModel" in js
    assert "isTerminal" in js
    assert "../orchestrator/status" in js


# ---- xstate scaffolding ------------------------------------------------

def test_uses_xstate_setup():
    js = _machine_js()
    assert "from \"xstate\"" in js
    assert "setup(" in js
    assert "createMachine(" in js


def test_declares_context_run_and_error():
    js = _machine_js()
    assert "run:" in js
    assert "error:" in js


# ---- events ------------------------------------------------------------

def test_handles_launch_event():
    js = _machine_js()
    assert "LAUNCH" in js


def test_handles_refresh_event():
    js = _machine_js()
    assert "REFRESH" in js


def test_handles_retry_step_event():
    js = _machine_js()
    assert "RETRY_STEP" in js


def test_handles_resume_from_event():
    js = _machine_js()
    assert "RESUME_FROM" in js


def test_handles_cancel_event():
    js = _machine_js()
    assert "CANCEL" in js


# ---- actors / invoked effects -----------------------------------------

def test_invokes_start_run_actor():
    js = _machine_js()
    assert "fromPromise" in js
    assert "startRun(" in js


def test_invokes_get_run_actor():
    js = _machine_js()
    assert "getRun(" in js


def test_invokes_retry_step_actor():
    js = _machine_js()
    assert "retryStep(" in js


def test_invokes_resume_run_actor():
    js = _machine_js()
    assert "resumeRun(" in js


def test_invokes_cancel_run_actor():
    js = _machine_js()
    assert "cancelRun(" in js


# ---- settles on terminal status ----------------------------------------

def test_settles_on_terminal_status():
    js = _machine_js()
    assert "isTerminal(" in js

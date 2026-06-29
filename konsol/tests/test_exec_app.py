"""TDD — konsol-exec "Execute" view wiring (E8).

E8 composes the now-built launch + timeline pieces into a real navigable SPA
subview driven by the E5 ``runExecMachine``. ``App.jsx`` registers an
``execute`` subview alongside the existing ``setup``/``monitor``/``history``
subviews, instantiates ``runExecMachine`` via the app's XState pattern
(``useMachine`` from ``@xstate/react``), and renders ``<ExecuteLaunch>`` (the
launch form) on top of ``<RunTimeline>`` (the live run), passing the machine
``send`` straight through and ``state.context.run`` to the timeline.

Following the repo's static-assertion convention (JSX isn't host-runnable),
these tests read ``App.jsx`` + ``constants.js`` and assert the imports, the
rendered components, the props passed through, and that the ``execute`` subview
is registered. All logic stays in the pure ESM core / the components.
"""
import os

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC_DIR = os.path.join(os.path.dirname(APP_DIR), "konsol-exec", "src")
APP_PATH = os.path.join(SRC_DIR, "App.jsx")
CONSTANTS_PATH = os.path.join(SRC_DIR, "constants.js")


def _app_js():
    with open(APP_PATH) as f:
        return f.read()


def _constants_js():
    with open(CONSTANTS_PATH) as f:
        return f.read()


# ---- file surface ------------------------------------------------------

def test_app_file_exists():
    assert os.path.exists(APP_PATH)


# ---- imports -----------------------------------------------------------

def test_app_imports_execute_launch():
    js = _app_js()
    assert "ExecuteLaunch" in js
    assert "./components/ExecuteLaunch" in js


def test_app_imports_run_timeline():
    js = _app_js()
    assert "RunTimeline" in js
    assert "./components/RunTimeline" in js


def test_app_imports_run_exec_machine():
    js = _app_js()
    assert "runExecMachine" in js
    assert "./machines" in js


def test_app_imports_use_machine():
    js = _app_js()
    assert "useMachine" in js
    assert "@xstate/react" in js


# ---- machine instantiation ---------------------------------------------

def test_app_instantiates_run_exec_machine():
    js = _app_js()
    assert "useMachine(runExecMachine)" in js


# ---- renders both components -------------------------------------------

def test_app_renders_execute_launch():
    js = _app_js()
    assert "<ExecuteLaunch" in js


def test_app_renders_run_timeline():
    js = _app_js()
    assert "<RunTimeline" in js


# ---- passes machine send + context.run --------------------------------

def test_app_passes_send_to_launch_and_timeline():
    js = _app_js()
    # both components receive a `send` prop dispatching machine events
    assert "send=" in js


def test_app_passes_context_run_to_timeline():
    js = _app_js()
    assert ("context.run" in js) or ("run={" in js)


# ---- execute subview registered ----------------------------------------

def test_execute_subview_registered():
    js = _app_js()
    consts = _constants_js()
    assert ('"execute"' in js) or ('"execute"' in consts)


def test_execute_subview_in_subnav_list():
    consts = _constants_js()
    # the execute tab is part of the domain sub-nav tab list
    assert '"execute"' in consts


def test_app_renders_execute_subview_branch():
    js = _app_js()
    # an active `execute` subview gates rendering of the execute plane
    assert 'subview === "execute"' in js

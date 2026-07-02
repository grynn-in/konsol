"""TDD — konsol-exec launch panel (E6).

E6 builds the React form component that launches an orchestrator run from the
konsol-exec Vite SPA. The component collects the launch fields (Fiscal Year /
Period, Scope, Pipeline, Full Refresh + Skip Airbyte Sync), runs the
flat form through the pure ESM core ``buildRunArgs`` (``orchestrator/params``)
to produce ``{definition, params}``, and dispatches a ``LAUNCH`` event with that
payload to the E5 ``runExecMachine`` (via a ``send`` prop, matching how existing
components dispatch XState events).

Following the repo's static-assertion convention (JSX isn't host-runnable),
these tests read the component source and assert the required fields, the
``buildRunArgs`` import + call, and the ``LAUNCH`` dispatch are present.
"""
import os

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC_DIR = os.path.join(os.path.dirname(APP_DIR), "konsol-exec", "src")
LAUNCH_PATH = os.path.join(SRC_DIR, "components", "ExecuteLaunch.jsx")


def _launch_js():
    with open(LAUNCH_PATH) as f:
        return f.read()


# ---- file + export surface ---------------------------------------------

def test_launch_file_exists():
    assert os.path.exists(LAUNCH_PATH)


def test_launch_exported_named():
    js = _launch_js()
    assert "export function ExecuteLaunch" in js


# ---- imports the pure core + react -------------------------------------

def test_imports_react():
    js = _launch_js()
    assert "react" in js


def test_imports_build_run_args():
    js = _launch_js()
    assert "buildRunArgs" in js
    assert "../orchestrator/params" in js


# ---- form fields -------------------------------------------------------

def test_renders_fiscal_year_field():
    js = _launch_js()
    assert "Fiscal Year" in js
    assert "fiscal_year" in js


def test_renders_fiscal_period_field():
    js = _launch_js()
    assert "Fiscal Period" in js
    assert "fiscal_period" in js


def test_renders_scope_field():
    js = _launch_js()
    assert "Scope" in js
    assert "scope" in js


def test_renders_definition_field():
    js = _launch_js()
    assert ">Pipeline<" in js
    assert "definition" in js


def test_renders_full_refresh_check():
    js = _launch_js()
    assert "Full Refresh" in js
    assert "full_refresh" in js


def test_no_per_run_skip_sync_checkbox():
    """Airbyte sync is governed by ONE global flag (EPM Settings.skip_airbyte_sync,
    read in run.run_pipeline), not a per-run checkbox — the launch form must not
    offer a per-run skip toggle."""
    js = _launch_js()
    assert 'name="skip_sync"' not in js
    # the form still tells the user where the global guard lives
    assert "EPM Settings" in js


def test_has_checkbox_inputs():
    js = _launch_js()
    assert "checkbox" in js


# ---- start run dispatches LAUNCH via buildRunArgs ----------------------

def test_has_start_run_button():
    js = _launch_js()
    assert "Start Run" in js


def test_calls_build_run_args():
    js = _launch_js()
    assert "buildRunArgs(" in js


def test_dispatches_launch_event():
    js = _launch_js()
    assert "LAUNCH" in js


def test_accepts_send_prop():
    js = _launch_js()
    assert "send" in js


# ---- the 4 scalar fields are dropdowns populated from the backend ------

def test_scalar_fields_are_selects():
    """Fiscal Year / Period, Scope, Pipeline must be <select>, not
    free-text <input> (one <select> per scalar field)."""
    js = _launch_js()
    assert js.count("<select") >= 4


def test_fetches_launch_options():
    js = _launch_js()
    assert "getLaunchOptions" in js
    assert "useEffect" in js


def test_options_have_default_blank_entry():
    """Each dropdown offers an empty 'all/default' choice so a blank run is
    possible (the params builder omits blanks)."""
    js = _launch_js()
    assert 'value=""' in js

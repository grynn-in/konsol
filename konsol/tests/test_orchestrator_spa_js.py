"""TDD — konsol-exec orchestrator SPA front-end (PRD-11).

PRD-11 ships the konsol-exec console: a launch form (fiscal year / period /
scope / ``full_refresh`` / ``skip_sync`` + optional definition) that POSTs to
``konsol.orchestrator.api.start_run``, plus a step timeline that renders the
Pipeline Run's ``steps`` child rows (PRD-6 fields) with live updates and
retry / resume / cancel buttons wired to the PRD-10 whitelisted API.

Following the established ``test_pipeline_run_js`` style, this is a pure
JS-presence test: it reads the page's JS (and its ``.json`` page def) and
asserts the form fields, the timeline render, the four ``api.*`` call sites and
the ``orchestrator_step`` realtime subscription are present. No frappe / no
browser needed for the host suite.
"""
import json
import os

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PAGE_DIR = os.path.join(APP_DIR, "pipeline", "page", "konsol_exec")
JS_PATH = os.path.join(PAGE_DIR, "konsol_exec.js")
JSON_PATH = os.path.join(PAGE_DIR, "konsol_exec.json")
INIT_PATH = os.path.join(PAGE_DIR, "__init__.py")


def _js():
    with open(JS_PATH) as f:
        return f.read()


# --- scaffold -------------------------------------------------------------

def test_page_dir_is_a_python_package():
    assert os.path.exists(INIT_PATH)


def test_js_file_exists():
    assert os.path.exists(JS_PATH)


def test_page_json_exists_and_is_a_page_doctype():
    assert os.path.exists(JSON_PATH)
    with open(JSON_PATH) as f:
        doc = json.load(f)
    assert doc["doctype"] == "Page"
    # belongs to a real konsol module so `bench migrate` can install it
    assert doc["module"] == "Pipeline"
    assert doc.get("standard") == "Yes"


def test_js_registers_the_page_load_hook():
    js = _js()
    assert "frappe.pages" in js
    assert "on_page_load" in js


# --- launch form ----------------------------------------------------------

def test_js_has_all_launch_form_fields():
    js = _js()
    for field in (
        "fiscal_year",
        "fiscal_period",
        "scope",
        "full_refresh",
        "skip_sync",
        "definition",
    ):
        assert field in js, field


# --- api call sites (PRD-10) ---------------------------------------------

def test_js_calls_start_run():
    assert "konsol.orchestrator.api.start_run" in _js()


def test_js_calls_retry_step():
    assert "konsol.orchestrator.api.retry_step" in _js()


def test_js_calls_resume_run():
    assert "konsol.orchestrator.api.resume_run" in _js()


def test_js_calls_cancel_run():
    assert "konsol.orchestrator.api.cancel_run" in _js()


# --- step timeline (PRD-6 fields) ----------------------------------------

def test_js_renders_step_timeline_fields():
    js = _js()
    for field in (
        "step_id",
        "step_type",
        "status",
        "started_at",
        "ended_at",
        "error",
    ):
        assert field in js, field


def test_js_has_timeline_render_function():
    assert "timeline" in _js().lower()


# --- live updates ---------------------------------------------------------

def test_js_subscribes_to_orchestrator_step_realtime():
    js = _js()
    assert "frappe.realtime.on" in js
    assert "orchestrator_step" in js


# --- action buttons -------------------------------------------------------

def test_js_has_retry_resume_cancel_buttons():
    js = _js().lower()
    assert "retry" in js
    assert "resume" in js
    assert "cancel" in js


# --- status indicators ----------------------------------------------------

def test_js_has_status_indicators():
    js = _js()
    assert "Success" in js
    assert "Failed" in js
    assert "Running" in js

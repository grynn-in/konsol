"""TDD tests for pipeline_run.js client script."""
import os

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
JS_PATH = os.path.join(
    APP_DIR, "pipeline", "doctype", "pipeline_run", "pipeline_run.js"
)


def test_js_file_exists():
    """Client script JS must exist."""
    assert os.path.exists(JS_PATH)


def test_js_has_list_view_button():
    """JS must define a list view button to trigger new run."""
    with open(JS_PATH) as f:
        content = f.read()
    assert "trigger_pipeline" in content


def test_js_has_realtime_listener():
    """JS must listen for pipeline_progress realtime events."""
    with open(JS_PATH) as f:
        content = f.read()
    assert "pipeline_progress" in content
    assert "realtime" in content


def test_js_has_form_refresh():
    """JS must handle form refresh to show status."""
    with open(JS_PATH) as f:
        content = f.read()
    assert "refresh" in content


def test_js_has_indicator_logic():
    """JS must show colored indicators based on status."""
    with open(JS_PATH) as f:
        content = f.read()
    # Should have indicator color mapping
    assert "green" in content or "Green" in content
    assert "red" in content or "Red" in content

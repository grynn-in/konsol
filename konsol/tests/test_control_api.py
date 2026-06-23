"""Static + light tests for Konsol Control API."""
import os

APP_DIR = os.path.join(os.path.dirname(__file__), "..")


def _src(path):
    with open(path) as f:
        return f.read()


def test_control_api_module_exists():
    assert os.path.isfile(os.path.join(APP_DIR, "control_api.py"))


def test_control_api_exposes_snapshot_and_start():
    src = _src(os.path.join(APP_DIR, "control_api.py"))
    assert "def get_snapshot" in src
    assert "def start_process" in src
    assert '"budgeting"' in src
    assert '"forecasting"' in src
    assert '"consolidation"' in src


def test_konsol_control_page_files_exist():
    page_dir = os.path.join(APP_DIR, "epm", "page", "konsol_control")
    assert os.path.isfile(os.path.join(page_dir, "konsol_control.json"))
    assert os.path.isfile(os.path.join(page_dir, "konsol_control.js"))
    assert "konsol-control" in _src(os.path.join(page_dir, "konsol_control.json"))


def test_konsol_control_doppio_assets_exist():
    doppio_dir = os.path.join(APP_DIR, "public", "js", "konsol_control")
    assert os.path.isfile(os.path.join(doppio_dir, "konsol_control.bundle.jsx"))
    assert os.path.isfile(os.path.join(doppio_dir, "App.jsx"))
    assert os.path.isfile(os.path.join(doppio_dir, "control.css"))
    page_js = _src(os.path.join(APP_DIR, "epm", "page", "konsol_control", "konsol_control.js"))
    assert "konsol_control.bundle.jsx" in page_js
    assert "on_page_show" in page_js


def test_dashboard_includes_konsol_control_shortcut():
    dash = _src(os.path.join(APP_DIR, "dashboard.py"))
    assert "Konsol Control" in dash
    assert "konsol-control" in dash
    assert "_PAGE_SHORTCUTS" in dash
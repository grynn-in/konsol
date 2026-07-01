"""Static + light tests for Konsol Control API and Exec SPA."""
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
    assert "def get_run_detail" in src
    assert '"runs": _domain_runs_all()' in src
    assert '"budgeting"' in src
    assert '"forecasting"' in src
    assert '"consolidation"' in src


def test_konsol_exec_www_route_exists():
    assert os.path.isfile(os.path.join(APP_DIR, "www", "konsol-exec.html"))
    # controller filename is underscore (Frappe maps route hyphens -> underscores);
    # see test_exec_www.py for the full guard (grynn-in/konsolidat#92-adjacent).
    assert os.path.isfile(os.path.join(APP_DIR, "www", "konsol_exec.py"))
    html = _src(os.path.join(APP_DIR, "www", "konsol-exec.html"))
    assert "/assets/konsol/konsol_exec/konsol_exec.js" in html
    assert "/assets/konsol/konsol_exec/konsol_exec.css" in html


def test_konsol_exec_spa_source_exists():
    app_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "konsol-exec"))
    assert os.path.isfile(os.path.join(app_root, "vite.config.js"))
    assert os.path.isfile(os.path.join(app_root, "src", "App.jsx"))
    assert os.path.isfile(os.path.join(app_root, "src", "api.js"))
    assert os.path.isfile(os.path.join(app_root, "src", "machines", "konsolAppMachine.js"))
    assert os.path.isfile(os.path.join(app_root, "src", "machines", "runDetailMachine.js"))
    pkg = _src(os.path.join(app_root, "package.json"))
    assert '"xstate"' in pkg
    assert '"@xstate/react"' in pkg


def test_hooks_register_konsol_exec_route():
    hooks = _src(os.path.join(APP_DIR, "hooks.py"))
    assert "website_route_rules" in hooks
    assert "/konsol-exec/" in hooks


def test_dashboard_links_konsol_exec():
    dash = _src(os.path.join(APP_DIR, "dashboard.py"))
    assert "Konsol Exec" in dash
    assert "/konsol-exec" in dash
    assert "_URL_SHORTCUTS" in dash
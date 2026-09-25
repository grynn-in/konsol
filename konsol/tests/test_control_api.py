"""Static + light tests for Konsol Control API.

konsol#305 R01 removed the konsol-exec SPA and its four checks here; R02
removes control_api and this file.
"""
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

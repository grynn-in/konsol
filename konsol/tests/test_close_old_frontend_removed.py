"""konsol#305 R01: the konsol-exec front end is gone; /close replaces it.

Static checks on the tree. Each one fails while any part of the old SPA,
its bundle, its www controller, its route rule or its Desk tile remains.
"""
import os

APP_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
REPO_DIR = os.path.dirname(APP_DIR)


def _read(path):
    with open(path) as f:
        return f.read()


def test_old_frontend_files_do_not_exist():
    present = [
        p
        for p in (
            os.path.join(REPO_DIR, "konsol-exec"),
            os.path.join(APP_DIR, "public", "konsol_exec"),
            os.path.join(APP_DIR, "www", "konsol_exec.py"),
            os.path.join(APP_DIR, "www", "konsol-exec.html"),
            os.path.join(REPO_DIR, "scripts", "hot-deploy-exec.sh"),
        )
        if os.path.exists(p)
    ]
    assert present == [], "still present: %s" % present


def test_hooks_have_no_konsol_exec_route():
    hooks = _read(os.path.join(APP_DIR, "hooks.py"))
    assert "konsol-exec" not in hooks
    assert "konsol_exec" not in hooks
    # the replacement route stays
    assert '{"from_route": "/close/<path:app_path>", "to_route": "close"}' in hooks


def test_dashboard_tile_points_at_close():
    dash = _read(os.path.join(APP_DIR, "dashboard.py"))
    assert "/konsol-exec" not in dash
    assert "Konsol Exec" not in dash
    assert '("Close", "/close", "Teal")' in dash

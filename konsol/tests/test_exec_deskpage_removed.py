"""E9 — the throwaway Frappe Desk page is retired (the real plane is the Vite SPA).

A previous mistake built a Desk page at ``konsol/pipeline/page/konsol_exec/``
(route ``/app/konsol-exec``). The execution plane now lives in the Vite SPA at
``/konsol-exec/`` (E1-E8). This static-assertion test guards that the Desk page
and its old test are gone and that no source file still references them.
"""
import os

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PAGE_DIR = os.path.join(APP_DIR, "pipeline", "page", "konsol_exec")
OLD_TEST = os.path.join(APP_DIR, "tests", "test_orchestrator_spa_js.py")

THIS_FILE = os.path.abspath(__file__)
SOURCE_EXTS = (".py", ".js", ".json", ".html", ".vue")
FORBIDDEN = (
    "pages/konsol_exec",
    'frappe.pages["konsol-exec"]',
    "frappe.pages['konsol-exec']",
)


def _source_files():
    for root, dirs, files in os.walk(APP_DIR):
        # never scan caches
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        for fname in files:
            if not fname.endswith(SOURCE_EXTS):
                continue
            path = os.path.join(root, fname)
            if os.path.abspath(path) == THIS_FILE:
                continue
            yield path


# --- artifacts are gone ---------------------------------------------------

def test_desk_page_dir_removed():
    assert not os.path.exists(PAGE_DIR)


def test_old_spa_js_test_removed():
    assert not os.path.exists(OLD_TEST)


# --- nothing references the dead surface ----------------------------------

def test_no_source_references_the_desk_page():
    offenders = []
    for path in _source_files():
        with open(path, encoding="utf-8", errors="ignore") as f:
            src = f.read()
        for needle in FORBIDDEN:
            if needle in src:
                offenders.append((path, needle))
    assert not offenders, offenders

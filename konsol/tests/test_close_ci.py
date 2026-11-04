"""TDD tests for konsol#305 B05 — CI runs the close-ui tests.

.github/workflows/tests.yml must gain a `close-ui-js-tests` job, a copy of the
existing `js-tests` job (tests.yml:38-52) but pointed at close-ui/ instead of
konsol-exec/. Parsed as plain text, not with PyYAML: a YAML loader would
normalize away an exact wrong string (for example a copy-pasted lockfile path
still naming konsol-exec), and that is exactly the mistake this test exists
to catch.
"""
import os
import re

APP_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
WORKFLOW_PATH = os.path.join(APP_DIR, ".github", "workflows", "tests.yml")

# A top-level job header looks like "  <name>:" with exactly two leading
# spaces and nothing after the colon. Nested keys (four-space "    steps:")
# and "key: value" pairs at two-space indent ("  contents: read") both fail
# this pattern, so it isolates job boundaries only.
_JOB_HEADER = re.compile(r"^  (\S[^\n]*):\s*$", re.MULTILINE)


def _job_block(text, job_name):
    """Return the text of one top-level job block, or None if it is absent."""
    matches = list(_JOB_HEADER.finditer(text))
    for i, m in enumerate(matches):
        if m.group(1) == job_name:
            start = m.start()
            end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
            return text[start:end]
    return None


def _read_workflow():
    with open(WORKFLOW_PATH) as f:
        return f.read()


def test_workflow_file_exists():
    assert os.path.exists(WORKFLOW_PATH)


def test_close_ui_job_exists():
    block = _job_block(_read_workflow(), "close-ui-js-tests")
    assert block is not None, "no close-ui-js-tests job in .github/workflows/tests.yml"


def test_close_ui_job_uses_close_ui_working_directory():
    block = _job_block(_read_workflow(), "close-ui-js-tests")
    assert block is not None
    assert "working-directory: close-ui" in block


def test_close_ui_job_runs_every_test_file():
    """Node 24 refuses a directory argument (`node --test src/` fails with
    "Cannot find module .../src"), so the file list is expanded by the shell
    in package.json's test script, which CI runs."""
    block = _job_block(_read_workflow(), "close-ui-js-tests")
    assert block is not None
    assert "run: yarn test" in block
    import json, os
    pkg = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "close-ui", "package.json")
    with open(pkg) as f:
        script = json.load(f)["scripts"]["test"]
    assert script == "node --test $(find src -name '*.test.mjs' | sort)"


def test_close_ui_job_uses_node_24():
    """Frappe v16 requires Node 24 (konsol#358)."""
    block = _job_block(_read_workflow(), "close-ui-js-tests")
    assert block is not None
    assert "node-version: '24'" in block


def test_close_ui_job_installs_with_frozen_lockfile():
    block = _job_block(_read_workflow(), "close-ui-js-tests")
    assert block is not None
    assert "yarn install --frozen-lockfile" in block


def test_close_ui_job_uses_close_ui_lockfile_path():
    """Failure path: a copy-paste of js-tests could leave the konsol-exec lockfile path."""
    block = _job_block(_read_workflow(), "close-ui-js-tests")
    assert block is not None
    assert "cache-dependency-path: close-ui/yarn.lock" in block
    assert "konsol-exec" not in block


def test_old_js_tests_job_is_removed():
    """konsol#305 R01 deleted konsol-exec, so its js-tests job must be gone too."""
    workflow = _read_workflow()
    assert _job_block(workflow, "js-tests") is None
    assert "konsol-exec" not in workflow

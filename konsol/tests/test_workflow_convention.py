"""The submittable / workflow convention (decided with the user, 12 Sep 2026).

1. Submit is the approval: review states are drafts (docstatus 0), the approve
   transition is the submit, and nothing changes after submit.
2. Cancel only while the period is open; a cancelled document leaves the
   warehouse.
3. Workflows are installed once: create if missing, never overwrite.

Enumerated across every workflow file and every controller in the app.
"""
import ast
import glob
import json
import os

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _workflows():
    for path in sorted(glob.glob(os.path.join(APP_DIR, "*", "doctype", "*", "*_workflow.json"))):
        with open(path) as f:
            yield os.path.basename(path), json.load(f)


def test_there_is_a_workflow_to_check():
    assert list(_workflows()), "no *_workflow.json found — the enumeration below would be vacuous"


def test_no_workflow_changes_a_submitted_document():
    """No transition from one submitted state to another: that is an
    update-after-submit, and it is what made every Consolidation Adjustment
    submit fail (#131)."""
    for name, wf in _workflows():
        status = {s["state"]: int(s["doc_status"]) for s in wf["states"]}
        for t in wf["transitions"]:
            assert not (status[t["state"]] == 1 and status[t["next_state"]] == 1), (
                f"{name}: {t['state']} -> {t['next_state']} is an update-after-submit")


def test_approval_is_the_submit_and_review_happens_in_draft():
    for name, wf in _workflows():
        status = {s["state"]: int(s["doc_status"]) for s in wf["states"]}
        submitted = [s for s, d in status.items() if d == 1]
        assert len(submitted) == 1, f"{name}: exactly one submitted state, got {submitted}"
        into_submit = [t for t in wf["transitions"] if status[t["next_state"]] == 1]
        assert into_submit and all(status[t["state"]] == 0 for t in into_submit), (
            f"{name}: the submit must come from a draft review state")


def _controllers():
    for py in glob.glob(os.path.join(APP_DIR, "*", "doctype", "*", "*.py")):
        if py.endswith("__init__.py") or "/tests/" in py:
            continue
        with open(py) as f:
            tree = ast.parse(f.read())
        for cls in [n for n in tree.body if isinstance(n, ast.ClassDef)]:
            yield os.path.relpath(py, APP_DIR), cls


def test_no_controller_saves_itself_on_submit_or_cancel():
    """self.save() in on_submit is an update-after-submit; in on_cancel it
    edits a cancelled document, which Frappe refuses. Set fields in
    before_submit / before_cancel instead."""
    offenders = []
    for path, cls in _controllers():
        for fn in [n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name in ("on_submit", "on_cancel")]:
            for call in [n for n in ast.walk(fn) if isinstance(n, ast.Call)]:
                f = call.func
                if isinstance(f, ast.Attribute) and f.attr == "save" and isinstance(f.value, ast.Name) and f.value.id == "self":
                    offenders.append(f"{path}:{cls.name}.{fn.name}")
    assert not offenders, offenders


def test_an_adjustment_is_reversed_only_in_an_open_period():
    for path, cls in _controllers():
        if cls.name == "ConsolidationAdjustment":
            fn = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == "before_cancel")
            assert "assert_open" in ast.unparse(fn)
            return
    raise AssertionError("ConsolidationAdjustment not found")


def test_workflows_are_installed_on_fresh_and_existing_sites_never_overwritten():
    with open(os.path.join(APP_DIR, "hooks.py")) as f:
        hooks = f.read()
    assert "konsol.workflows.install_workflows" in hooks.split("after_install")[1].split("\n")[0]
    with open(os.path.join(APP_DIR, "install.py")) as f:
        after = f.read().split("def after_migrate")[1].split("\ndef ")[0]
    assert "_install_workflows()" in after
    with open(os.path.join(APP_DIR, "workflows.py")) as f:
        src = f.read()
    body = src.split("def install_workflows")[1]
    assert 'frappe.db.exists("Workflow", {"document_type": doctype})' in body, (
        "skip a doctype that already has any workflow: never overwrite a site's own")


def test_every_installed_workflow_has_a_definition():
    import re
    with open(os.path.join(APP_DIR, "workflows.py")) as f:
        installed = re.findall(r'"([^"]+)"', f.read().split("INSTALLED = (")[1].split(")")[0])
    defined = {wf["document_type"] for _, wf in _workflows()}
    assert installed and set(installed) <= defined, (installed, defined)

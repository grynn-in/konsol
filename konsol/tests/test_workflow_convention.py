"""The submittable / workflow convention (decided with the user, 12 Sep 2026).

1. Submit is the approval: review states are drafts (docstatus 0), the approve
   transition is the submit, and nothing changes after submit.
2. Cancel only while the period is open; a cancelled document leaves the
   warehouse.
3. Workflows are installed once: create if missing, never overwrite.

Enumerated across every workflow file and every controller in the app.
"""
import ast
import contextlib
import glob
import importlib.util
import json
import os
import sys
import types

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


def _is_submittable(document_type):
    """Read is_submittable from the doctype's own <snake>.json."""
    snake = document_type.lower().replace(" ", "_")
    found = glob.glob(os.path.join(APP_DIR, "*", "doctype", snake, f"{snake}.json"))
    assert len(found) == 1, f"{document_type}: expected one doctype json, found {found}"
    with open(found[0]) as f:
        return bool(json.load(f).get("is_submittable"))


def test_approval_is_the_submit_and_review_happens_in_draft():
    checked = 0
    for name, wf in _workflows():
        if not _is_submittable(wf["document_type"]):
            continue
        checked += 1
        status = {s["state"]: int(s["doc_status"]) for s in wf["states"]}
        submitted = [s for s, d in status.items() if d == 1]
        assert len(submitted) == 1, f"{name}: exactly one submitted state, got {submitted}"
        into_submit = [t for t in wf["transitions"] if status[t["next_state"]] == 1]
        assert into_submit and all(status[t["state"]] == 0 for t in into_submit), (
            f"{name}: the submit must come from a draft review state")
    assert checked, "no workflow on a submittable doctype — the check above would be vacuous"


def test_a_workflow_on_a_non_submittable_doctype_never_submits_or_cancels():
    """A doctype that can't be submitted has no docstatus 1 or 2: every state
    of its workflow must stay a draft (doc_status "0")."""
    for name, wf in _workflows():
        if _is_submittable(wf["document_type"]):
            continue
        bad = [s["state"] for s in wf["states"] if str(s["doc_status"]) != "0"]
        assert not bad, f"{name}: non-submittable doctype, states not doc_status 0: {bad}"


#: Submittable doctypes whose controller does not call set_cancelled_state,
#: with the reason. An exempt doctype's workflow must still reach its
#: docstatus-2 state by a transition (checked below): apply_workflow sets the
#: next state before it calls cancel(). Build Approval is not here: it is not
#: submittable, so it is never cancelled, and the test above keeps all its
#: states at doc_status 0.
CANCEL_STATE_EXEMPT = {
    # J06 (konsol#305) is kept (coordinator, 15 Nov 2026, on #529): the Reverse
    # transition is the governed path and sets Reversed; before_cancel refuses a
    # direct cancel whose status is not a docstatus-2 state, so a script's
    # doc.cancel() never lands an approved state at docstatus 2.
    "Consolidation Journal": "J06: a direct cancel is refused; the Reverse transition sets Reversed",
}


def _controller_for(document_type):
    snake = document_type.lower().replace(" ", "_")
    found = glob.glob(os.path.join(APP_DIR, "*", "doctype", snake, f"{snake}.py"))
    assert len(found) == 1, f"{document_type}: expected one controller, found {found}"
    with open(found[0]) as f:
        src = f.read()
    tree = ast.parse(src)
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef)
               and n.name == document_type.replace(" ", ""))
    return os.path.relpath(found[0], APP_DIR), src, cls


def test_a_submittable_workflow_has_a_docstatus_2_state_that_the_cancel_sets():
    """konsol#529, option (b): Frappe's plain Cancel sets docstatus 2 and no
    workflow state, and no shipped workflow had a transition into its
    docstatus-2 state, so a cancelled document showed "Approved". Every
    submittable workflow has a docstatus-2 state, and its controller's
    before_cancel calls konsol.workflows.set_cancelled_state(self), which
    reads that state from the Workflow as the site has it. A doctype that
    may skip this is named in CANCEL_STATE_EXEMPT, with its reason."""
    checked = 0
    for name, wf in _workflows():
        doctype = wf["document_type"]
        if not _is_submittable(doctype):
            continue
        checked += 1
        cancelled = [s["state"] for s in wf["states"] if str(s["doc_status"]) == "2"]
        assert cancelled, f"{name}: no docstatus-2 state; a cancelled document would keep its approved state"
        if doctype in CANCEL_STATE_EXEMPT:
            assert any(t["next_state"] in cancelled for t in wf["transitions"]), (
                f"{name}: exempt from set_cancelled_state, so a transition must reach {cancelled}")
            continue
        path, src, cls = _controller_for(doctype)
        assert "from konsol.workflows import set_cancelled_state" in src, f"{path}: import the shared helper"
        before_cancel = next((n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == "before_cancel"), None)
        assert before_cancel is not None, f"{path}: {cls.name} has no before_cancel"
        assert "set_cancelled_state(self)" in ast.unparse(before_cancel), (
            f"{path}: {cls.name}.before_cancel does not set the docstatus-2 state")
    assert checked, "no submittable workflow checked — the test would be vacuous"


def test_cancel_state_exemptions_name_a_real_submittable_workflow():
    workflow_doctypes = {wf["document_type"] for _, wf in _workflows() if _is_submittable(wf["document_type"])}
    assert set(CANCEL_STATE_EXEMPT) <= workflow_doctypes, set(CANCEL_STATE_EXEMPT) - workflow_doctypes
    assert all(CANCEL_STATE_EXEMPT.values()), "every exemption carries its reason"


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


def test_a_journal_is_reversed_only_in_an_open_period():
    for path, cls in _controllers():
        if cls.name == "ConsolidationJournal":
            fn = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == "before_cancel")
            assert "assert_open" in ast.unparse(fn)
            return
    raise AssertionError("ConsolidationJournal not found")


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


def test_no_endpoint_names_the_retired_adjustment():
    """konsol#305 J11: konsol-exec (the endpoints' only UI caller) is dumped,
    and approval_api.approve is the one approve path (decided 27 Sep: no
    third approve path). approve_adjustment / reverse_adjustment and every
    "Consolidation Adjustment" mention leave api.py; approval_api's comment
    about the old endpoint goes too."""
    with open(os.path.join(APP_DIR, "api.py")) as f:
        api_src = f.read()
    assert "def approve_adjustment" not in api_src
    assert "def reverse_adjustment" not in api_src
    assert "Consolidation Adjustment" not in api_src
    with open(os.path.join(APP_DIR, "close", "approval_api.py")) as f:
        approval_api_src = f.read()
    assert "approve_adjustment" not in approval_api_src


def test_a_journal_cannot_skip_the_workflow_or_carry_an_approval_into_a_draft():
    """konsol#305 J06: the journal keeps the adjustment's guards, now that
    its own workflow is installed."""
    for path, cls in _controllers():
        if cls.name != "ConsolidationJournal":
            continue
        methods = {n.name: ast.unparse(n) for n in cls.body if isinstance(n, ast.FunctionDef)}
        assert "approved_by = self.approved_at = None" in methods["before_insert"]
        assert "_states(0)" in methods["validate"] and "frappe.throw" in methods["validate"]
        for hook, docstatus in (("before_submit", 1), ("before_cancel", 2)):
            assert f"_states({docstatus})" in methods[hook] and "get_workflow_name" in methods[hook], hook
        # after_delete drives the warehouse sync (#120); on_trash would re-send the row
        assert "on_trash" not in methods
        return
    raise AssertionError("ConsolidationJournal not found")


#: The retired Consolidation Adjustment Workflow's states and transitions, as
#: shipped at close-d2 0965b57 (konsol#305 J12 deleted its JSON). J06 gave the
#: journal exactly these; the literal keeps that pin now the file is gone.
_RETIRED_ADJUSTMENT_WORKFLOW = {
    "states": [
        {"state": "Draft", "doc_status": "0", "allow_edit": "EPM Analyst"},
        {"state": "Pending Approval", "doc_status": "0", "allow_edit": "EPM Admin"},
        {"state": "Approved", "doc_status": "1", "allow_edit": "EPM Admin"},
        {"state": "Reversed", "doc_status": "2", "allow_edit": "EPM Analyst"},
    ],
    "transitions": [
        {"state": "Draft", "action": "Send for Approval", "next_state": "Pending Approval",
         "allowed": "EPM Analyst", "allow_self_approval": 1},
        {"state": "Pending Approval", "action": "Reject", "next_state": "Draft",
         "allowed": "EPM Admin", "allow_self_approval": 1},
        {"state": "Pending Approval", "action": "Approve", "next_state": "Approved",
         "allowed": "EPM Admin", "allow_self_approval": 1},
        {"state": "Approved", "action": "Reverse", "next_state": "Reversed",
         "allowed": "EPM Admin", "allow_self_approval": 1},
    ],
}


def test_the_journal_workflow_is_installed_and_mirrors_the_adjustment():
    """konsol#305 J06 (#305-D2-1, R1): the journal takes the adjustment's
    workflow, states, roles and all. allow_self_approval stays 1: R5 is the
    runtime hook's (konsol.close.self_approval), not the JSON's. J12 deleted
    the adjustment's JSON; its shape is kept above as a literal."""
    import re
    with open(os.path.join(APP_DIR, "workflows.py")) as f:
        installed = re.findall(r'"([^"]+)"', f.read().split("INSTALLED = (")[1].split(")")[0])
    assert "Consolidation Journal" in installed, installed
    by_type = {wf["document_type"]: wf for _, wf in _workflows()}
    assert "Consolidation Journal" in by_type, "no consolidation_journal_workflow.json"
    assert "Consolidation Adjustment" not in by_type, "the retired workflow JSON is back"
    journal, adjustment = by_type["Consolidation Journal"], _RETIRED_ADJUSTMENT_WORKFLOW
    assert journal["name"] == journal["workflow_name"] == "Consolidation Journal Workflow"
    assert journal["workflow_state_field"] == "status" and journal["is_active"] == 1
    assert journal["states"] == adjustment["states"]
    assert journal["transitions"] == adjustment["transitions"]
    assert all(t["allow_self_approval"] == 1 for t in journal["transitions"])


def test_a_doctype_that_grants_amend_can_be_amended():
    """Amend writes amended_from. Consolidation Adjustment granted amend but
    had no such field, so every amend raised "Unknown column" (#134 walk)."""
    missing = []
    for path in glob.glob(os.path.join(APP_DIR, "*", "doctype", "*", "*.json")):
        with open(path) as f:
            try:
                d = json.load(f)
            except ValueError:
                continue
        if d.get("doctype") != "DocType" or not d.get("is_submittable"):
            continue
        if any(p.get("amend") for p in d.get("permissions", [])):
            if not any(f["fieldname"] == "amended_from" for f in d["fields"]):
                missing.append(d["name"])
    assert not missing, missing


@contextlib.contextmanager
def _stub_frappe(**attrs):
    stub = types.ModuleType("frappe")
    for k, v in attrs.items():
        setattr(stub, k, v)
    saved = sys.modules.get("frappe")
    sys.modules["frappe"] = stub
    try:
        yield stub
    finally:
        if saved is None:
            sys.modules.pop("frappe", None)
        else:
            sys.modules["frappe"] = saved


def _load(relpath, name):
    spec = importlib.util.spec_from_file_location(name, os.path.join(APP_DIR, relpath))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_workflows_install_and_never_rewrite():
    """konsol#305 J12a (Problems P13): the role-upgrade code loses its only
    user (Consolidation Adjustment, now retired from INSTALLED) and is
    deleted. A workflow install creates a missing workflow and otherwise
    leaves an installed one alone; it never rewrites one."""
    with open(os.path.join(APP_DIR, "workflows.py")) as f:
        src = f.read()
    tree = ast.parse(src)
    defined = {n.name for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    assigned = {t.id for n in tree.body if isinstance(n, ast.Assign)
                for t in n.targets if isinstance(t, ast.Name)}
    gone = {"PREVIOUSLY_SHIPPED_ROLES", "planned_role_upgrade", "_upgrade_roles", "_grant_previous_approvers"}
    assert not (gone & (defined | assigned)), gone & (defined | assigned)

    import re
    installed = re.findall(r'"([^"]+)"', src.split("INSTALLED = (")[1].split(")")[0])
    assert "Consolidation Adjustment" not in installed, installed
    assert "Consolidation Journal" in installed, installed

    def _refused(*a, **k):
        raise AssertionError("install_workflows must not call get_doc when every workflow already exists")

    with _stub_frappe(
        get_app_path=lambda app: APP_DIR,
        db=types.SimpleNamespace(exists=lambda *a, **k: True),
        get_doc=_refused,
    ):
        wf = _load("workflows.py", "_wf_no_rewrite_under_test")
        result = wf.install_workflows()
    assert result == []



def test_install_workflows_can_be_narrowed_to_named_doctypes():
    """konsol#592 T25b: the Rate Sheet migration patch installs Rate Sheet's
    workflow only, before the model sync, so a workflow for a doctype whose
    fields have not synced yet is not created early. Still create-if-missing."""
    created = []

    def get_doc(d):
        return types.SimpleNamespace(insert=lambda **k: created.append(d.get("doctype") and
                                                                       (d["doctype"], d.get("document_type"))))

    def exists(doctype, filters=None, *a, **k):
        if doctype == "DocType":
            return True
        if doctype == "Workflow":
            return False
        return True  # states and actions exist

    with _stub_frappe(get_app_path=lambda app: APP_DIR, db=types.SimpleNamespace(exists=exists),
                      get_doc=get_doc):
        wf = _load("workflows.py", "_wf_narrow_under_test")
        wf.install_workflows(doctypes=("Rate Sheet",))
    assert created == [("Workflow", "Rate Sheet")], created

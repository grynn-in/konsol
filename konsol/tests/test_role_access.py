"""Roles and access (F7, decided with the user 12 Sep 2026).

The screen shows job titles; the code checks these roles:

  Close Lead        EPM Admin          runs the close, approves (submits)
  Group Accountant  EPM Analyst        drafts adjustments, rates, ownership, IC
  Entity Accountant Entity Accountant  trial balances and base budget, own entities
  Budget Reviewer   Budget Controller / Manager / Approver
  Viewer            EPM User           reads

Until F7 the core consolidation doctypes were System Manager only, so no
business role could do consolidation work, and four roles were named in
permissions and code but never created by the app.
"""
import ast
import contextlib
import glob
import importlib.util
import json
import os
import re
import sys
import types

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FLAGS = {"r": "read", "w": "write", "c": "create", "d": "delete", "s": "submit", "x": "cancel", "a": "amend"}
FRAPPE_ROLES = {"System Manager", "Administrator", "Guest", "All"}


def _meta(doctype):
    snake = doctype.lower().replace(" ", "_")
    (path,) = glob.glob(os.path.join(APP_DIR, "*", "doctype", snake, snake + ".json"))
    with open(path) as f:
        return json.load(f)


def _perm(doctype, role):
    rows = [p for p in _meta(doctype).get("permissions", []) if p.get("role") == role and not p.get("permlevel")]
    return "".join(ch for ch, field in FLAGS.items() if any(p.get(field) for p in rows))


def _workflow(doctype):
    snake = doctype.lower().replace(" ", "_")
    (path,) = glob.glob(os.path.join(APP_DIR, "*", "doctype", snake, snake + "_workflow.json"))
    with open(path) as f:
        return json.load(f)


def _assigned_dict(path, name):
    with open(path) as f:
        tree = ast.parse(f.read())
    node = next(n for n in tree.body if isinstance(n, ast.Assign)
                and any(getattr(t, "id", None) == name for t in n.targets))
    return ast.literal_eval(node.value)


def _created_roles():
    return set(_assigned_dict(os.path.join(APP_DIR, "install.py"), "ROLES"))


def test_every_role_the_app_names_is_created():
    named = {}
    for path in glob.glob(os.path.join(APP_DIR, "*", "doctype", "*", "*.json")):
        with open(path) as f:
            meta = json.load(f)
        if not isinstance(meta, dict):
            continue
        for p in meta.get("permissions") or []:
            named.setdefault(p.get("role"), os.path.relpath(path, APP_DIR))
        for s in meta.get("states") or []:
            if isinstance(s, dict) and s.get("allow_edit"):
                named.setdefault(s["allow_edit"], os.path.relpath(path, APP_DIR))
        for t in meta.get("transitions") or []:
            named.setdefault(t.get("allowed"), os.path.relpath(path, APP_DIR))
    sheet = os.path.join(APP_DIR, "epm", "doctype", "budget_sheet", "budget_sheet.py")
    for role in _assigned_dict(sheet, "LAYER_ROLES").values():
        named.setdefault(role, "budget_sheet.py LAYER_ROLES")
    for aliases in _assigned_dict(sheet, "LAYER_ROLE_ALIASES").values():
        for role in aliases:
            named.setdefault(role, "budget_sheet.py LAYER_ROLE_ALIASES")
    with open(os.path.join(APP_DIR, "control_api.py")) as f:
        for role in re.findall(r'owner="([^"]+)"', f.read()):
            named.setdefault(role, "control_api.py owner=")
    missing = {r: where for r, where in named.items() if r and r not in FRAPPE_ROLES and r not in _created_roles()}
    assert not missing, f"named but never created by install.ROLES: {missing}"


def test_after_install_creates_roles_before_workflows():
    """A workflow transition links to its role, so on a fresh site the roles
    must exist first."""
    hooks = _assigned_dict(os.path.join(APP_DIR, "hooks.py"), "after_install")
    assert hooks.index("konsol.install.create_roles") < hooks.index("konsol.workflows.install_workflows")


# The agreed matrix for the doctypes F7 opened up. Exact, so a widening or a
# loss both show up here rather than in production.
MATRIX = {
    "Trial Balance Submission": {"EPM Admin": "rwcdsxa", "Entity Accountant": "rwcsxa", "EPM Analyst": "r", "EPM User": "r"},
    "Consolidation Adjustment": {"EPM Admin": "rwcsxa", "EPM Analyst": "rwcd", "EPM User": "r"},
    "Historical Equity Rate": {"EPM Admin": "rwcdsx", "EPM Analyst": "rwcs", "EPM User": "r"},
    "Ownership Period": {"EPM Admin": "rwcdsx", "EPM Analyst": "rwc", "EPM User": "r"},
    "IC Balance": {"EPM Admin": "rwcdsx", "EPM Analyst": "rwcsx", "EPM User": "r"},
    "IC Elimination Rule": {"EPM Admin": "rwcd", "EPM Analyst": "r"},
    "Consolidation Group": {"EPM Admin": "rwcd", "EPM Analyst": "r", "EPM User": "r"},
    "Allocation Run": {"EPM Admin": "rwcdsx", "EPM Analyst": "rwc", "EPM User": "r"},
    "Allocation Rule": {"EPM Admin": "rwcd", "EPM Analyst": "rwc", "EPM User": "r"},
    "Allocation Driver": {"EPM Admin": "rwcd", "EPM Analyst": "rwc", "EPM User": "r"},
    "Pipeline Run": {"EPM Admin": "rw", "EPM Analyst": "r", "EPM User": "r"},
    "Assertion Run": {"EPM Admin": "rwc", "EPM Analyst": "r", "EPM User": "r"},
    "Period Status": {"EPM Admin": "rwc", "EPM Analyst": "r", "EPM User": "r", "Entity Accountant": "r"},
    "Budget Sheet": {"Budget Submitter": "rwc", "Entity Accountant": "rwc", "Budget Controller": "rw",
                     "Budget Manager": "rw", "Budget Approver": "rw"},
    "Budget Cycle": {"Budget Manager": "rwcsx", "Budget Submitter": "r", "Budget Controller": "r",
                     "Budget Approver": "r", "Entity Accountant": "r"},
    "Entity": {"EPM Admin": "rwc", "EPM Analyst": "r", "EPM User": "r", "Entity Accountant": "r",
               "Budget Submitter": "r", "Budget Controller": "r", "Budget Manager": "r", "Budget Approver": "r"},
}


def test_access_matrix():
    wrong = []
    for doctype, grants in MATRIX.items():
        for role, want in grants.items():
            got = _perm(doctype, role)
            if got != want:
                wrong.append(f"{doctype} / {role}: want {want!r}, got {got!r}")
    assert not wrong, "\n".join(wrong)


def test_entity_accountant_gets_only_entity_scoped_write():
    """Entity Accountant is scoped by entity through data_area_id. Write on a
    doctype without that scoping would reach every entity."""
    scoped = set(_assigned_dict(os.path.join(APP_DIR, "entity_permissions.py"), "ENTITY_SCOPED_DOCTYPES"))
    for path in glob.glob(os.path.join(APP_DIR, "*", "doctype", "*", "*.json")):
        with open(path) as f:
            meta = json.load(f)
        if not isinstance(meta, dict) or meta.get("doctype") != "DocType":
            continue
        rows = [p for p in meta.get("permissions") or [] if p.get("role") == "Entity Accountant"]
        if any(p.get(k) for p in rows for k in ("write", "create", "submit", "cancel", "delete")):
            assert meta["name"] in scoped, f"Entity Accountant can write {meta['name']}, which is not entity-scoped"


def test_adjustment_workflow_analyst_drafts_admin_approves():
    wf = _workflow("Consolidation Adjustment")
    status = {s["state"]: int(s["doc_status"]) for s in wf["states"]}
    allowed = {t["action"]: t["allowed"] for t in wf["transitions"]}
    assert allowed == {"Send for Approval": "EPM Analyst", "Reject": "EPM Admin",
                       "Approve": "EPM Admin", "Reverse": "EPM Admin"}
    # submit is the approval, so the approving role holds submit and the
    # drafting role does not
    for t in wf["transitions"]:
        if status[t["next_state"]] == 1:
            assert "s" in _perm("Consolidation Adjustment", t["allowed"])
        if status[t["next_state"]] == 2:
            assert "x" in _perm("Consolidation Adjustment", t["allowed"])
        assert "w" in _perm("Consolidation Adjustment", t["allowed"]), t
    assert "s" not in _perm("Consolidation Adjustment", "EPM Analyst")


def test_only_the_close_lead_approves_consolidation_work():
    for doctype in ("Consolidation Adjustment", "Ownership Period", "Allocation Run", "Trial Balance Submission"):
        submitters = {p["role"] for p in _meta(doctype).get("permissions", []) if p.get("submit")}
        assert submitters <= {"System Manager", "Administrator", "EPM Admin", "Entity Accountant"}, (doctype, submitters)
        assert "EPM Analyst" not in submitters, doctype


@contextlib.contextmanager
def _stub_frappe(**attrs):
    stub = types.ModuleType("frappe")
    stub.session = types.SimpleNamespace(user="u@example.com")
    stub.PermissionError = PermissionError
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


def _allowed_for(roles, assigned=(), restrict=False):
    with _stub_frappe(
        get_roles=lambda user=None: list(roles),
        permissions=types.SimpleNamespace(
            get_user_permissions=lambda user: {"Entity": [{"doc": d} for d in assigned]}),
        get_cached_value=lambda *a, **k: 1 if restrict else 0,
    ):
        mod = _load("entity_permissions.py", "_ep_under_test")
        mod.subtree_codes = lambda codes: set(codes)
        return mod.allowed_entity_codes("u@example.com")


def test_unassigned_entity_accountant_sees_nothing():
    assert _allowed_for(["Entity Accountant"]) == set()
    assert _allowed_for(["Entity Accountant", "EPM User"]) == set()


def test_group_roles_keep_the_frappe_default():
    assert _allowed_for(["EPM User"]) is None
    assert _allowed_for(["EPM Analyst"]) is None
    assert _allowed_for(["Entity Accountant", "EPM Analyst"]) is None
    assert _allowed_for(["Entity Accountant", "System Manager"]) is None


def test_assigned_entity_accountant_sees_their_entities():
    assert _allowed_for(["Entity Accountant"], assigned=["AMAT"]) == {"AMAT"}


def test_restrict_by_default_still_applies_to_everyone():
    assert _allowed_for(["EPM User"], restrict=True) == set()


def _planner():
    with _stub_frappe():
        return _load("workflows.py", "_wf_under_test")


def test_untouched_shipped_workflow_takes_the_new_roles():
    wf = _planner()
    definition = _workflow("Consolidation Adjustment")
    states = [(s["state"], "System Manager") for s in definition["states"]]
    transitions = [(t["state"], t["action"], "System Manager") for t in definition["transitions"]]
    plan = wf.planned_role_upgrade(states, transitions, definition, wf.PREVIOUSLY_SHIPPED_ROLES["Consolidation Adjustment"])
    assert plan is not None
    edit, allowed = plan
    assert allowed[("Pending Approval", "Approve")] == "EPM Admin"
    assert allowed[("Draft", "Send for Approval")] == "EPM Analyst"
    assert edit["Draft"] == "EPM Analyst"


def test_a_customised_workflow_is_left_alone():
    wf = _planner()
    definition = _workflow("Consolidation Adjustment")
    prev = wf.PREVIOUSLY_SHIPPED_ROLES["Consolidation Adjustment"]
    states = [(s["state"], "System Manager") for s in definition["states"]]
    transitions = [(t["state"], t["action"], "System Manager") for t in definition["transitions"]]
    # a site gave one transition to its own role
    custom = list(transitions)
    custom[0] = (custom[0][0], custom[0][1], "Finance Controller")
    assert wf.planned_role_upgrade(states, custom, definition, prev) is None
    # a site added its own state
    assert wf.planned_role_upgrade(states + [("On Hold", "System Manager")], transitions, definition, prev) is None
    # already upgraded: nothing to do
    current_states = [(s["state"], s["allow_edit"]) for s in definition["states"]]
    current_tr = [(t["state"], t["action"], t["allowed"]) for t in definition["transitions"]]
    assert wf.planned_role_upgrade(current_states, current_tr, definition, prev) is None

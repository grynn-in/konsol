"""The budgeting "run" approves through the workflow (konsol#215 row W5).

control_api.start_process("budgeting") inserts a Build Approval. With the
Build Approval Workflow active, after_insert has already taken "Request" on
a fresh load, so the in-memory row is stale: start_process reloads it and,
when it waits in Pending Review, takes "Approve" only if the workflow offers
that transition to this user. The workflow's roles decide, not a hardcoded
"System Manager" check. A site without an active workflow keeps the old
System Manager shortcut. The response carries the row's final state.

control_api.py is loaded by path against stub frappe / konsol modules.
"""
import ast
import contextlib
import copy
import importlib.util
import os
import sys
import types

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CA_PATH = os.path.join(APP_DIR, "control_api.py")
BA_MODULE = "konsol.pipeline.doctype.build_approval.build_approval"


class _D(dict):
    """frappe._dict: attribute reads are item reads."""
    __getattr__ = dict.get


class _Site:
    """A stub site: Build Approval rows, the session, the workflow module."""

    def __init__(self, workflow=True, user="zz.user@example.com", roles=("EPM Analyst",)):
        self.workflow = workflow
        self.roles = set(roles)
        self.rows = {}
        self.applied = []
        self.inserted = []
        site = self

        class ValidationError(Exception):
            pass

        frappe = types.ModuleType("frappe")
        frappe.whitelist = lambda *a, **k: (lambda fn: fn)
        frappe.ValidationError = ValidationError
        frappe._ = lambda s: s
        frappe._dict = _D
        frappe.session = _D(user=user)
        frappe.get_roles = lambda *a, **k: sorted(site.roles)

        def throw(msg, exc=ValidationError, *a, **k):
            raise exc(msg)
        frappe.throw = throw
        frappe.db = types.SimpleNamespace(commit=lambda: None, get_value=lambda *a, **k: None)

        class BuildApproval:
            """A Build Approval row. insert() runs what the real hooks do:
            without a workflow, before_save moves a high-risk Draft to
            Pending Review in memory; with one, after_insert takes Request
            on a fresh load, so only the stored row moves."""

            def __init__(self, fields):
                self.__dict__.update(fields)

            def fields(self):
                return {k: v for k, v in self.__dict__.items() if not k.startswith("_")}

            def insert(self, ignore_permissions=False):
                self.name = "ZZ-BA-0001"
                self.approved_by = None
                site.inserted.append(self.fields())
                if not site.workflow and self.workflow_state == "Draft":
                    self.workflow_state = "Pending Review"  # scenarios is high risk
                site.rows[self.name] = copy.deepcopy(self.fields())
                if site.workflow:
                    site.rows[self.name]["workflow_state"] = "Pending Review"
                return self

            def reload(self):
                self.__dict__.update(copy.deepcopy(site.rows[self.name]))

            def save(self, ignore_permissions=False):
                site.rows[self.name] = copy.deepcopy(self.fields())
                return self

        def get_doc(*args, **kwargs):
            spec = args[0] if args else kwargs
            assert isinstance(spec, dict) and spec.get("doctype") == "Build Approval", spec
            return BuildApproval(dict(spec))
        frappe.get_doc = get_doc

        def get_transitions(doc):
            """The shipped workflow from Pending Review: Approve and Reject, EPM Admin only."""
            if doc.workflow_state != "Pending Review" or "EPM Admin" not in site.roles:
                return []
            return [_D(state="Pending Review", action="Approve", next_state="Approved", allowed="EPM Admin"),
                    _D(state="Pending Review", action="Reject", next_state="Cancelled", allowed="EPM Admin")]

        def apply_workflow(doc, action):
            site.applied.append(action)
            transition = next((t for t in get_transitions(doc) if t.action == action), None)
            if not transition:
                raise ValidationError("Not a valid Workflow Action")
            doc.workflow_state = transition.next_state
            doc.approved_by = frappe.session.user  # before_save's rule for a high-risk row
            doc.save()
            return doc

        model = types.ModuleType("frappe.model")
        workflow_mod = types.ModuleType("frappe.model.workflow")
        workflow_mod.get_transitions = get_transitions
        workflow_mod.apply_workflow = apply_workflow
        model.workflow = workflow_mod
        frappe.model = model

        utils = types.ModuleType("frappe.utils")
        utils.add_to_date = utils.get_datetime = utils.now_datetime = utils.today = lambda *a, **k: None
        frappe.utils = utils

        konsol = types.ModuleType("konsol")
        konsol.__path__ = []
        budget_sheet = types.ModuleType("konsol.epm.doctype.budget_sheet.budget_sheet")
        budget_sheet.LAYER_ROLES = {}
        schema_lifecycle = types.ModuleType("konsol.schema_lifecycle")
        schema_lifecycle.check_epm_admin = lambda: None
        ba = types.ModuleType(BA_MODULE)
        ba.workflow_active = lambda: bool(site.workflow)

        self.mods = {
            "frappe": frappe, "frappe.utils": utils, "frappe.model": model,
            "frappe.model.workflow": workflow_mod, "konsol": konsol,
            "konsol.epm.doctype.budget_sheet.budget_sheet": budget_sheet,
            "konsol.schema_lifecycle": schema_lifecycle, BA_MODULE: ba,
        }
        with self.installed():
            spec = importlib.util.spec_from_file_location("_control_api_w5", CA_PATH)
            module = importlib.util.module_from_spec(spec)
            try:
                spec.loader.exec_module(module)
            except Exception as exc:  # noqa: BLE001 — never let this read as a skip
                raise AssertionError(f"control_api.py failed to load on stubs: {exc!r}") from exc
        self.api = module

    @contextlib.contextmanager
    def installed(self):
        saved = {m: sys.modules.get(m) for m in self.mods}
        sys.modules.update(self.mods)
        try:
            yield
        finally:
            for m, old in saved.items():
                if old is None:
                    sys.modules.pop(m, None)
                else:
                    sys.modules[m] = old

    def run(self):
        with self.installed():
            return self.api.start_process("budgeting", fiscal_year=2026)


# --- with the workflow active -----------------------------------------------

def test_an_epm_admin_approves_through_the_workflow():
    site = _Site(user="zz.admin@example.com", roles=("EPM Admin",))
    out = site.run()
    assert site.applied == ["Approve"]
    assert site.rows["ZZ-BA-0001"]["workflow_state"] == "Approved"
    assert site.rows["ZZ-BA-0001"]["approved_by"] == "zz.admin@example.com"
    assert out["state"] == "Approved"
    assert out["name"] == "ZZ-BA-0001" and out["run_kind"] == "pbr"


def test_an_epm_analyst_leaves_the_row_in_pending_review():
    site = _Site(user="zz.analyst@example.com", roles=("EPM Analyst",))
    out = site.run()
    assert site.applied == []
    assert site.rows["ZZ-BA-0001"]["workflow_state"] == "Pending Review"
    assert out["state"] == "Pending Review"


def test_a_system_manager_without_the_approve_transition_does_not_approve():
    site = _Site(user="zz.sysmgr@example.com", roles=("System Manager",))
    out = site.run()
    assert site.applied == []
    assert site.rows["ZZ-BA-0001"]["workflow_state"] == "Pending Review"
    assert site.rows["ZZ-BA-0001"]["approved_by"] is None
    assert out["state"] == "Pending Review"


def test_the_row_is_inserted_as_a_draft_request():
    site = _Site(roles=("EPM Admin",))
    site.run()
    row = site.inserted[0]
    assert row["workflow_state"] == "Draft"
    assert row["build_scope"] == "scenarios"


# --- a site without an active workflow ----------------------------------------

def test_without_a_workflow_a_system_manager_still_approves_directly():
    site = _Site(workflow=False, user="zz.sysmgr@example.com", roles=("System Manager",))
    out = site.run()
    assert site.applied == []
    assert site.rows["ZZ-BA-0001"]["workflow_state"] == "Approved"
    assert site.rows["ZZ-BA-0001"]["approved_by"] == "zz.sysmgr@example.com"
    assert out["state"] == "Approved"


def test_without_a_workflow_anyone_else_leaves_it_pending():
    site = _Site(workflow=False, user="zz.admin@example.com", roles=("EPM Admin",))
    out = site.run()
    assert site.applied == []
    assert site.rows["ZZ-BA-0001"]["workflow_state"] == "Pending Review"
    assert out["state"] == "Pending Review"


# --- source -----------------------------------------------------------------

def _start_process():
    with open(CA_PATH) as fh:
        tree = ast.parse(fh.read())
    return next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "start_process")


def test_start_process_writes_no_workflow_state_itself():
    fn = _start_process()
    writes = [
        node.lineno for node in ast.walk(fn)
        if isinstance(node, (ast.Assign, ast.AugAssign, ast.AnnAssign))
        for target in (node.targets if isinstance(node, ast.Assign) else [node.target])
        if isinstance(target, ast.Attribute) and target.attr == "workflow_state"
    ]
    assert writes == [], f"start_process assigns workflow_state at line(s) {writes}"


def test_start_process_has_no_system_manager_check():
    fn = _start_process()
    strings = [n.value for n in ast.walk(fn) if isinstance(n, ast.Constant) and isinstance(n.value, str)]
    assert "System Manager" not in strings

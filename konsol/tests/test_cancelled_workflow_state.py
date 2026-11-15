"""A cancelled document reaches its workflow's docstatus-2 state (konsol#529).

Chosen on #529 under the standing rule, option (b): Frappe's plain Cancel sets
docstatus 2 and nothing else, so every submittable workflow konsol installs
left a cancelled document showing "Approved". ``before_cancel`` now sets the
state field to the installed workflow's docstatus-2 state, read from the
Workflow as the site has it (a site may rename the states, 12 Sep 2026,
point 3). No shipped state name is written anywhere. Warehouse decisions still
read docstatus.

The helper is ``konsol.workflows.set_cancelled_state``; the patch
``set_cancelled_workflow_state`` repairs rows cancelled before this fix. Both
run here against a stub frappe that models a site.
"""
import importlib.util
import os
import sys
import types

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORKFLOWS_PY = os.path.join(APP_DIR, "workflows.py")
PATCH_PY = os.path.join(APP_DIR, "patches", "set_cancelled_workflow_state.py")
DOTTED = "konsol.patches.set_cancelled_workflow_state"


def _state(name, doc_status):
    return types.SimpleNamespace(state=name, doc_status=doc_status)


def _workflow(doctype, field, states, name=None, is_active=1):
    return types.SimpleNamespace(
        name=name or f"{doctype} Workflow", document_type=doctype, is_active=is_active,
        workflow_state_field=field, states=[_state(s, d) for s, d in states])


class _Site:
    """Active workflows plus rows by doctype: {doctype: {name: {field: value}}}."""

    def __init__(self, workflows=(), rows=None):
        self.workflows = list(workflows)
        self.rows = {dt: {n: dict(r) for n, r in by.items()} for dt, by in (rows or {}).items()}
        self.set_values = []

    def frappe(self):
        site = self
        frappe = types.ModuleType("frappe")

        def get_value(doctype, filters=None, fieldname=None, *a, **k):
            assert doctype == "Workflow" and fieldname == "name", (doctype, filters, fieldname)
            for wf in site.workflows:
                if wf.document_type == filters["document_type"] and wf.is_active == filters["is_active"]:
                    return wf.name
            return None

        def get_cached_doc(doctype, name):
            assert doctype == "Workflow"
            return next(wf for wf in site.workflows if wf.name == name)

        def get_all(doctype, filters=None, fields=None, **k):
            out = []
            for n, r in site.rows.get(doctype, {}).items():
                if all(r.get(f) == v for f, v in (filters or {}).items()):
                    row = {"name": n}
                    row.update({f: r.get(f) for f in fields or () if f != "name"})
                    out.append(row)
            return out

        def set_value(doctype, name, field, value, update_modified=True, **k):
            assert update_modified is False, "a repair must leave modified alone"
            site.rows[doctype][name][field] = value
            site.set_values.append((doctype, name, field, value))

        frappe.db = types.SimpleNamespace(
            get_value=get_value, set_value=set_value,
            exists=lambda doctype, name=None, **k: doctype == "DocType" and name in site.rows,
            table_exists=lambda doctype: doctype in site.rows)
        frappe.get_cached_doc = get_cached_doc
        frappe.get_all = get_all
        frappe.get_app_path = lambda app: APP_DIR
        return frappe


def _load(path, name, frappe):
    saved = {k: sys.modules.get(k) for k in ("frappe", "konsol.workflows")}
    sys.modules["frappe"] = frappe
    sys.modules.pop("konsol.workflows", None)
    try:
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        if name == "konsol.workflows":
            sys.modules[name] = module
        spec.loader.exec_module(module)
        return module
    finally:
        for k, v in saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v


def _workflows_module(site):
    return _load(WORKFLOWS_PY, "konsol.workflows", site.frappe())


def _doc(doctype, **fields):
    return types.SimpleNamespace(doctype=doctype, **fields)


# -- the pure read ------------------------------------------------------------

def test_cancelled_states_are_the_docstatus_2_states_in_workflow_order():
    wf = _workflows_module(_Site())
    states = [("Draft", "0"), ("Pending Approval", "0"), ("Approved", "1"), ("Cancelled", "2")]
    assert wf.cancelled_states(states) == ["Cancelled"]
    assert wf.cancelled_states([("Draft", 0), ("Reversed", 2), ("Void", 2)]) == ["Reversed", "Void"]
    assert wf.cancelled_states([("Draft", "0"), ("Approved", "1")]) == []
    assert wf.cancelled_states([]) == []


# -- set_cancelled_state, from before_cancel -----------------------------------

def test_a_plain_cancel_lands_the_installed_docstatus_2_state():
    site = _Site([_workflow("Business Combination", "status",
                            [("Draft", "0"), ("Pending Approval", "0"), ("Approved", "1"), ("Cancelled", "2")])])
    wf = _workflows_module(site)
    doc = _doc("Business Combination", status="Approved", docstatus=2)
    assert wf.set_cancelled_state(doc) == "Cancelled"
    assert doc.status == "Cancelled"


def test_the_state_is_read_from_the_site_workflow_not_a_shipped_name():
    """A site renamed the states and keeps them in workflow_state: the renamed
    docstatus-2 state is what the document gets, in the site's field."""
    site = _Site([_workflow("Budget Cycle", "workflow_state",
                            [("Entwurf", "0"), ("Freigegeben", "1"), ("Storniert", "2")], name="Site Cycle WF")])
    wf = _workflows_module(site)
    doc = _doc("Budget Cycle", workflow_state="Freigegeben", status="Approved")
    assert wf.set_cancelled_state(doc) == "Storniert"
    assert doc.workflow_state == "Storniert"
    assert doc.status == "Approved", "the workflow's own field, not a guess"


def test_a_state_a_transition_already_set_is_kept():
    """apply_workflow sets the next state before it calls doc.cancel(). With
    two docstatus-2 states the transition's choice stands; the helper does not
    overwrite it with the first."""
    site = _Site([_workflow("Consolidation Journal", "status",
                            [("Draft", "0"), ("Approved", "1"), ("Reversed", "2"), ("Voided", "2")])])
    wf = _workflows_module(site)
    doc = _doc("Consolidation Journal", status="Voided")
    assert wf.set_cancelled_state(doc) == "Voided"
    assert doc.status == "Voided"


def test_no_active_workflow_sets_nothing():
    site = _Site([_workflow("Budget Cycle", "workflow_state",
                            [("Draft", "0"), ("Approved", "1"), ("Cancelled", "2")], is_active=0)])
    wf = _workflows_module(site)
    doc = _doc("Budget Cycle", workflow_state="Approved")
    assert wf.set_cancelled_state(doc) is None
    assert doc.workflow_state == "Approved"
    assert wf.cancel_target("Budget Cycle") is None


def test_a_workflow_without_a_docstatus_2_state_sets_nothing():
    """Build Approval's shape: every state is a draft. Nothing to set, and
    the caller can tell (None), so no silent fallback to a made-up name."""
    site = _Site([_workflow("Build Approval", "workflow_state",
                            [("Draft", "0"), ("Pending Review", "0"), ("Approved", "0"), ("Cancelled", "0")])])
    wf = _workflows_module(site)
    doc = _doc("Build Approval", workflow_state="Approved")
    assert wf.set_cancelled_state(doc) is None
    assert doc.workflow_state == "Approved"
    assert wf.cancel_target("Build Approval") is None


def test_a_blank_workflow_state_field_means_frappes_default():
    """Frappe reads a blank workflow_state_field as "workflow_state"."""
    site = _Site([_workflow("Budget Cycle", None, [("Draft", "0"), ("Approved", "1"), ("Cancelled", "2")])])
    wf = _workflows_module(site)
    assert wf.cancel_target("Budget Cycle") == ("workflow_state", ["Cancelled"])


# -- the patch ---------------------------------------------------------------

def _patched_site():
    return _Site(
        workflows=[
            _workflow("Business Combination", "status",
                      [("Draft", "0"), ("Pending Approval", "0"), ("Approved", "1"), ("Cancelled", "2")]),
            _workflow("Budget Cycle", "workflow_state",
                      [("Draft", "0"), ("Pending Approval", "0"), ("Approved", "1"), ("Cancelled", "2")]),
            _workflow("Build Approval", "workflow_state",
                      [("Draft", "0"), ("Approved", "0"), ("Cancelled", "0")]),
        ],
        rows={
            "Business Combination": {
                "BC-1": {"docstatus": 2, "status": "Approved"},     # the #529 shape
                "BC-2": {"docstatus": 2, "status": "Cancelled"},    # already right
                "BC-3": {"docstatus": 1, "status": "Approved"},     # submitted: untouched
                "BC-4": {"docstatus": 0, "status": "Draft"},
            },
            "Budget Cycle": {
                "CYC-1": {"docstatus": 2, "workflow_state": "Approved"},
                "CYC-2": {"docstatus": 2, "workflow_state": None},  # a blank Link is NULL
                "CYC-3": {"docstatus": 1, "workflow_state": "Approved"},
            },
            "Build Approval": {
                "BA-1": {"docstatus": 0, "workflow_state": "Approved"},
            },
            # Business Disposal: in INSTALLED, but this site has no workflow for it
            "Business Disposal": {
                "BD-1": {"docstatus": 2, "status": "Approved"},
            },
        })


def _run_patch(site):
    frappe = site.frappe()
    _load(WORKFLOWS_PY, "konsol.workflows", frappe)
    saved = {k: sys.modules.get(k) for k in ("frappe", "konsol.workflows")}
    sys.modules["frappe"] = frappe
    wf_spec = importlib.util.spec_from_file_location("konsol.workflows", WORKFLOWS_PY)
    wf_module = importlib.util.module_from_spec(wf_spec)
    sys.modules["konsol.workflows"] = wf_module
    try:
        wf_spec.loader.exec_module(wf_module)
        spec = importlib.util.spec_from_file_location("patch_529_under_test", PATCH_PY)
        patch = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(patch)
        patch.execute()
    finally:
        for k, v in saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v


def test_the_patch_is_registered():
    with open(os.path.join(APP_DIR, "patches.txt")) as f:
        lines = [line.strip() for line in f]
    assert DOTTED in lines, f"{DOTTED} not in patches.txt"
    assert os.path.exists(PATCH_PY)


def test_the_patch_repairs_cancelled_rows_left_in_a_non_cancelled_state():
    site = _patched_site()
    _run_patch(site)
    assert sorted(site.set_values) == [
        ("Budget Cycle", "CYC-1", "workflow_state", "Cancelled"),
        ("Budget Cycle", "CYC-2", "workflow_state", "Cancelled"),
        ("Business Combination", "BC-1", "status", "Cancelled"),
    ]
    assert site.rows["Business Combination"]["BC-3"]["status"] == "Approved"
    assert site.rows["Build Approval"]["BA-1"]["workflow_state"] == "Approved"
    assert site.rows["Business Disposal"]["BD-1"]["status"] == "Approved", (
        "no active workflow on this site: nothing to read the state from, nothing written")


def test_the_patch_is_idempotent():
    site = _patched_site()
    _run_patch(site)
    first = list(site.set_values)
    _run_patch(site)
    assert site.set_values == first, "a second run wrote again"


def test_the_patch_reads_the_doctypes_from_installed():
    """The patch covers konsol.workflows.INSTALLED, through the one helper:
    no doctype list and no state name of its own."""
    with open(PATCH_PY) as f:
        src = f.read()
    assert "INSTALLED" in src and "cancel_target" in src
    for literal in ('"Cancelled"', '"Reversed"', '"Approved"'):
        assert literal not in src, f"{literal} written in the patch: read the site's workflow instead"

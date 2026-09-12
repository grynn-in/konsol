"""Consolidation Adjustment's lifecycle hooks, run against a stub frappe (#134 re-review).

test_workflow_convention checks the shape of the code; these run the hooks, so
inverted logic fails here and not only in a live walk."""
import importlib.util
import os
import sys
import types

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CA = os.path.join(APP_DIR, "consolidation", "doctype", "consolidation_adjustment", "consolidation_adjustment.py")
WF = [("Draft", 0), ("Pending Approval", 0), ("Approved", 1), ("Reversed", 2)]


class Refused(Exception):
    pass


def _load(states=None, period_open=True):
    """Import the controller with frappe stubbed. ``states`` is the active
    workflow's [(state, doc_status)], or None for no workflow."""
    checked = []
    wf = types.SimpleNamespace(states=[types.SimpleNamespace(state=s, doc_status=str(d)) for s, d in states]) if states else None

    def throw(msg, *args, **kwargs):
        raise Refused(msg)

    def assert_open(fiscal_year, fiscal_period, action="run"):
        checked.append((fiscal_year, fiscal_period))
        if not period_open:
            raise Refused("period closed")

    class Document:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    mods = {name: types.ModuleType(name) for name in (
        "frappe", "frappe.model", "frappe.model.document", "frappe.model.workflow", "frappe.utils",
        "konsol", "konsol.clickhouse", "konsol.period_status")}
    frappe = mods["frappe"]
    frappe._ = lambda s: s
    frappe.throw = throw
    frappe.session = types.SimpleNamespace(user="approver@example.com")
    frappe.get_cached_doc = lambda doctype, name: wf
    frappe.db = types.SimpleNamespace(after_commit=types.SimpleNamespace(add=lambda fn: None, _functions=[]))
    mods["frappe.model.document"].Document = Document
    mods["frappe.model.workflow"].get_workflow_name = lambda doctype: "CA Workflow" if wf else None
    mods["frappe.utils"].cint = lambda v: int(v or 0)
    mods["frappe.utils"].now_datetime = lambda: "NOW"
    mods["konsol.clickhouse"].sync_doctype = lambda *a: None
    mods["konsol.period_status"].assert_open = assert_open

    saved = {name: sys.modules.get(name) for name in mods}
    sys.modules.update(mods)
    try:
        spec = importlib.util.spec_from_file_location("ca_under_test", CA)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        for name, old in saved.items():
            if old is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = old
    return module, checked


def _doc(module, **fields):
    base = dict(doctype="Consolidation Adjustment", status="Draft", docstatus=0, fiscal_year=2024,
                fiscal_period=12, approved_by=None, approved_at=None)
    return module.ConsolidationAdjustment(**dict(base, **fields))


def _refused(fn):
    try:
        fn()
    except Refused:
        return True
    return False


def test_without_a_workflow_submit_approves_and_stamps_the_approver():
    module, _ = _load()
    d = _doc(module, status="Pending Approval", approved_by="someone-else", approved_at="then")
    d.before_submit()
    assert (d.status, d.approved_by, d.approved_at) == ("Approved", "approver@example.com", "NOW")


def test_under_a_workflow_a_direct_submit_is_refused_and_the_workflows_approve_passes():
    module, _ = _load(WF)
    for status in ("Draft", "Pending Approval"):
        assert _refused(_doc(module, status=status).before_submit), status
    d = _doc(module, status="Approved")   # apply_workflow sets the state, then submits
    d.before_submit()
    assert (d.status, d.approved_by) == ("Approved", "approver@example.com")


def test_reverse_is_refused_in_a_closed_period_and_changes_nothing():
    for states in (None, WF):
        module, checked = _load(states, period_open=False)
        d = _doc(module, status="Reversed" if states else "Approved", docstatus=1)
        before = d.status
        assert _refused(d.before_cancel) and d.status == before and checked == [(2024, 12)]


def test_reverse_in_an_open_period():
    module, _ = _load()
    d = _doc(module, status="Approved", docstatus=1)
    d.before_cancel()
    assert d.status == "Reversed"
    module, _ = _load(WF)
    assert _refused(_doc(module, status="Approved", docstatus=1).before_cancel)   # a direct cancel
    d = _doc(module, status="Reversed", docstatus=1)                               # the workflow's Reverse
    d.before_cancel()
    assert d.status == "Reversed"


def test_a_draft_cannot_be_saved_into_a_submitted_state():
    for states in (None, WF):
        module, _ = _load(states)
        for status in ("Approved", "Reversed"):
            assert _refused(_doc(module, status=status, docstatus=0).validate), (states, status)
        for status in ("Draft", "Pending Approval"):
            _doc(module, status=status, docstatus=0).validate()
        _doc(module, status="Approved", docstatus=1).validate()


def test_every_new_adjustment_starts_in_the_first_state_with_no_approver():
    for states, first in ((None, "Draft"), (WF, "Draft"), ([("Entwurf", 0), ("Genehmigt", 1)], "Entwurf")):
        module, _ = _load(states)
        for status in ("Draft", "Pending Approval", "Approved", "Reversed", ""):
            d = _doc(module, status=status, approved_by="someone", approved_at="then")
            d.before_insert()
            assert (d.status, d.approved_by, d.approved_at) == (first, None, None), (states, status)

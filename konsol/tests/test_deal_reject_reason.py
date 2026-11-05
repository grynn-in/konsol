"""konsol#310: the server refuses a Business Combination or Business Disposal
Reject that carries no reason.

Consolidation Journal has refused one since konsol#305 J06a. The deal
doctypes did not: a Reject through the workflow bar, the REST API or a plain
apply_workflow call recorded no reason. konsol/close/reject_reason.py now
holds the rule, and both deal controllers call it first in validate.
"""
import importlib.util
import os
import sys
import types

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.join(_HERE, "..")
_SRC = os.path.join(_ROOT, "close", "reject_reason.py")
_DEALS = {
    "Business Combination": os.path.join(
        _ROOT, "consolidation", "doctype", "business_combination", "business_combination.py"),
    "Business Disposal": os.path.join(
        _ROOT, "consolidation", "doctype", "business_disposal", "business_disposal.py"),
}
_APPROVAL_API = os.path.join(_ROOT, "close", "approval_api.py")


class Refused(Exception):
    pass


def _throw(msg, *a, **k):
    raise Refused(msg)


_WF = types.SimpleNamespace(
    workflow_state_field="status",
    states=[types.SimpleNamespace(state="Draft", doc_status="0"),
            types.SimpleNamespace(state="Pending Approval", doc_status="0"),
            types.SimpleNamespace(state="Approved", doc_status="1")],
)


def _load(workflow=_WF):
    frappe = types.ModuleType("frappe")
    frappe.flags = {}
    frappe.throw = _throw
    frappe.get_cached_doc = lambda doctype, name: workflow
    frappe._ = lambda s: s
    model = types.ModuleType("frappe.model")
    wfmod = types.ModuleType("frappe.model.workflow")
    wfmod.get_workflow_name = lambda doctype: "WF" if workflow else None
    utils = types.ModuleType("frappe.utils")
    utils.cint = lambda v: int(v or 0)
    saved = {k: sys.modules.get(k) for k in ("frappe", "frappe.model", "frappe.model.workflow", "frappe.utils")}
    sys.modules.update({"frappe": frappe, "frappe.model": model,
                        "frappe.model.workflow": wfmod, "frappe.utils": utils})
    try:
        spec = importlib.util.spec_from_file_location("reject_reason_under_test", _SRC)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    finally:
        for k, v in saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v
    return mod, frappe


def _doc(doctype, name, status, before_status=None, docstatus=0):
    before = types.SimpleNamespace(status=before_status) if before_status else None
    return types.SimpleNamespace(
        doctype=doctype, name=name, status=status, docstatus=docstatus,
        get=lambda field, _s=status: _s if field == "status" else None,
        get_doc_before_save=lambda: before)


def test_a_reasonless_reject_of_either_deal_is_refused():
    for doctype in _DEALS:
        mod, frappe = _load()
        d = _doc(doctype, "ZZ-1", "Draft", before_status="Pending Approval")
        try:
            mod.refuse_reasonless_reject(d)
        except Refused as e:
            assert "reason" in str(e) and doctype in str(e), str(e)
        else:
            raise AssertionError(f"{doctype}: a reject with no reason was not refused")


def test_a_reject_through_approval_api_carries_the_flag_and_passes():
    for doctype in _DEALS:
        mod, frappe = _load()
        frappe.flags[mod.REJECT_REASON_FLAG] = {(doctype, "ZZ-1"): "ZZ wrong fair value"}
        mod.refuse_reasonless_reject(_doc(doctype, "ZZ-1", "Draft", before_status="Pending Approval"))


def test_the_flag_counts_only_for_the_document_it_names():
    mod, frappe = _load()
    frappe.flags[mod.REJECT_REASON_FLAG] = {("Business Combination", "ZZ-OTHER"): "x"}
    try:
        mod.refuse_reasonless_reject(
            _doc("Business Combination", "ZZ-1", "Draft", before_status="Pending Approval"))
    except Refused:
        pass
    else:
        raise AssertionError("another document's flag let this reject through")


def test_other_saves_are_unaffected():
    mod, frappe = _load()
    # a first save, a draft edit, a send for approval, a submitted document
    mod.refuse_reasonless_reject(_doc("Business Combination", "ZZ-1", "Draft"))
    mod.refuse_reasonless_reject(_doc("Business Combination", "ZZ-1", "Draft", before_status="Draft"))
    mod.refuse_reasonless_reject(
        _doc("Business Combination", "ZZ-1", "Pending Approval", before_status="Draft"))
    mod.refuse_reasonless_reject(
        _doc("Business Combination", "ZZ-1", "Approved", before_status="Pending Approval", docstatus=1))


def test_a_site_that_renamed_its_workflow_states_is_judged_by_its_own_states():
    """Workflows are installed once and a site may customise them (CLAUDE.md,
    decision 3): the rule reads the active workflow, never fixed names."""
    wf = types.SimpleNamespace(
        workflow_state_field="status",
        states=[types.SimpleNamespace(state="Prepared", doc_status="0"),
                types.SimpleNamespace(state="In Review", doc_status="0"),
                types.SimpleNamespace(state="Signed", doc_status="1")])
    mod, frappe = _load(wf)
    try:
        mod.refuse_reasonless_reject(
            _doc("Business Disposal", "ZZ-1", "Prepared", before_status="In Review"))
    except Refused:
        pass
    else:
        raise AssertionError("a renamed workflow's reject was not refused")


def test_with_no_active_workflow_there_is_no_state_to_move_and_nothing_to_refuse():
    """approval_api._reject_draft leaves a no-workflow draft unsaved."""
    mod, frappe = _load(workflow=None)
    mod.refuse_reasonless_reject(
        _doc("Business Combination", "ZZ-1", "Draft", before_status="Pending Approval"))


def test_both_deal_controllers_call_the_rule_first_in_validate():
    for doctype, path in _DEALS.items():
        with open(path) as f:
            src = f.read()
        body = src[src.index("    def validate(self):"):]
        body = body[:body.index("\n    def ", 10)]
        first = [ln.strip() for ln in body.splitlines()[1:]
                 if ln.strip() and not ln.strip().startswith(("#", '"""'))][0]
        assert first == "refuse_reasonless_reject(self)", f"{doctype}: {first}"
        assert "from konsol.close.reject_reason import refuse_reasonless_reject" in src, doctype


def test_the_reject_reason_flag_is_named_the_same_as_approval_api_sets_it():
    with open(_SRC) as f:
        rule = f.read()
    with open(_APPROVAL_API) as f:
        api = f.read()
    assert 'REJECT_REASON_FLAG = "konsol_reject_reason"' in rule
    assert 'REJECT_REASON_FLAG = "konsol_reject_reason"' in api

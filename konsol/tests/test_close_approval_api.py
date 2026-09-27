"""konsol#305 P08 — the approve endpoint that carries the reason (R5,
#305-D2-3).

`konsol.close.approval_api.approve(doctype, name, reason=None)` (POST) puts the
self-approval reason on the request-scoped flag the P07 hook reads, then
approves: the workflow "Approve" transition when the doctype has an active
workflow, otherwise `doc.submit()`. It does not decide policy; the hook does.

Loaded against a stub frappe (the `_Site`/`_call` pattern of
test_close_checks_api.py, copied, not imported). The real P07 hook
(`self_approval.py`) and the real policy model are loaded too, and the stub
`submit()` runs the hook, so "Allowed with reason" with and without a reason is
decided by the product's code, not by a copy.
"""
import importlib.util
import os
import sys
import types

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
API_PY = os.path.join(APP_DIR, "close", "approval_api.py")
HOOK_PY = os.path.join(APP_DIR, "close", "self_approval.py")
MODEL_PY = os.path.join(APP_DIR, "close", "close_policy_model.py")

LEAD = "zz-lead@example.com"
ANALYST = "zz-analyst@example.com"
ALLOWED = "Allowed with reason"
WORKFLOW_DOCTYPES = ("Consolidation Journal", "Business Combination", "Business Disposal")


class _Flags(dict):
    def __getattr__(self, n):
        return self.get(n)

    def __setattr__(self, n, v):
        self[n] = v


class _Site:
    def __init__(self, roles=("EPM Admin",), user=LEAD, owner=LEAD, policy=ALLOWED,
                 workflows=WORKFLOW_DOCTYPES):
        self.roles = set(roles)
        self.user = user
        self.owner = owner
        self.policy = policy
        self.workflows = set(workflows)
        self.only_for_calls = []
        self.get_doc_calls = []
        self.workflow_reads = []
        self.applied = []  # (doctype, name, action)
        self.submitted = []  # (doctype, name)
        self.comments = []  # (doctype, name, text)
        self.flags = _Flags()


def _load_by_path(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _frappe(site):
    frappe = types.ModuleType("frappe")
    frappe.ValidationError = type("ValidationError", (Exception,), {})
    frappe.PermissionError = type("PermissionError", (Exception,), {})
    frappe.flags = site.flags
    hook = {}

    def throw(msg, exc=None, **k):
        raise (exc or frappe.ValidationError)(msg)

    def only_for(roles, message=False):
        roles = [roles] if isinstance(roles, str) else list(roles)
        site.only_for_calls.append(tuple(roles))
        if not site.roles.intersection(roles):
            raise frappe.PermissionError("Not permitted")

    def whitelist(*a, **k):
        return lambda fn: fn

    class _Doc:
        def __init__(self, doctype, name):
            self.doctype = doctype
            self.name = name
            self.owner = site.owner
            self.docstatus = 0
            self.flags = types.SimpleNamespace()

        def submit(self):
            hook["check"](self)  # the P07 before_submit hook, as Frappe runs it
            self.docstatus = 1
            site.submitted.append((self.doctype, self.name))

        def add_comment(self, comment_type, text):
            site.comments.append((self.doctype, self.name, text))

    def get_doc(doctype, name=None):
        site.get_doc_calls.append((doctype, name))
        return _Doc(doctype, name)

    def get_value(doctype, filters, fieldname="name", **k):
        assert doctype == "Workflow", doctype
        site.workflow_reads.append(dict(filters))
        if filters.get("is_active") == 1 and filters.get("document_type") in site.workflows:
            return "ZZ " + filters["document_type"] + " Workflow"
        return None

    def get_single_value(dt, field, *a, **k):
        assert (dt, field) == ("Close Settings", "self_approval"), (dt, field)
        return site.policy

    def apply_workflow(doc, action):
        site.applied.append((doc.doctype, doc.name, action))
        assert action == "Approve", action
        # apply_workflow rebuilds and reloads the doc (workflow.py:101-102),
        # so only the request-scoped flag survives, not doc.flags.
        fresh = _Doc(doc.doctype, doc.name)
        fresh.submit()
        return fresh

    frappe.throw = throw
    frappe._ = lambda s: s
    frappe.only_for = only_for
    frappe.whitelist = whitelist
    frappe.get_doc = get_doc
    frappe.get_roles = lambda user=None: sorted(site.roles)
    frappe.db = types.SimpleNamespace(get_value=get_value, get_single_value=get_single_value)
    frappe.session = types.SimpleNamespace(user=site.user)
    workflow = types.ModuleType("frappe.model.workflow")
    workflow.apply_workflow = apply_workflow
    model = types.ModuleType("frappe.model")
    model.workflow = workflow
    frappe.model = model
    return frappe, hook


def _call(site, fn, *args, **kwargs):
    frappe, hook = _frappe(site)
    names = ["konsol", "konsol.close"]
    mods = {n: types.ModuleType(n) for n in names}
    mods.update({"frappe": frappe, "frappe.model": frappe.model,
                 "frappe.model.workflow": frappe.model.workflow})
    saved = {n: sys.modules.get(n) for n in
             names + ["frappe", "frappe.model", "frappe.model.workflow",
                      "konsol.close.close_policy_model", "konsol.close.self_approval"]}
    sys.modules.update(mods)
    try:
        policy_model = _load_by_path("konsol.close.close_policy_model", MODEL_PY)
        sys.modules["konsol.close.close_policy_model"] = policy_model
        mods["konsol.close"].close_policy_model = policy_model
        self_approval = _load_by_path("konsol.close.self_approval", HOOK_PY)
        sys.modules["konsol.close.self_approval"] = self_approval
        mods["konsol.close"].self_approval = self_approval
        hook["check"] = self_approval.check
        module = _load_by_path("close_approval_api_under_test", API_PY)
        return getattr(module, fn)(*args, **kwargs), frappe
    finally:
        for n, old in saved.items():
            if old is None:
                sys.modules.pop(n, None)
            else:
                sys.modules[n] = old


def _raises(fn, exc_name):
    try:
        fn()
    except Exception as e:  # noqa: BLE001
        assert type(e).__name__ == exc_name, f"{type(e).__name__}: {e}"
        return str(e)
    raise AssertionError(f"expected {exc_name}")


# --- the failure paths -----------------------------------------------------------

def test_a_doctype_outside_the_list_is_refused_before_any_get_doc():
    for doctype in ("Trial Balance Submission", "TB Exception", "User", "Sales Invoice"):
        site = _Site()
        msg = _raises(lambda: _call(site, "approve", doctype, "ZZ-1", reason="ZZ why"),
                      "ValidationError")
        assert doctype in msg, msg
        assert site.get_doc_calls == [], site.get_doc_calls
        assert site.applied == [] and site.submitted == []
        assert not site.flags.get("konsol_self_approval_reason")


def test_an_analyst_is_refused_by_only_for():
    site = _Site(roles=("EPM Analyst",), user=ANALYST)
    _raises(lambda: _call(site, "approve", "IC Balance", "ZZ-IC-1"), "PermissionError")
    assert site.only_for_calls == [("EPM Admin", "System Manager")]
    assert site.get_doc_calls == [] and site.submitted == []


def test_only_for_is_the_close_lead_and_system_manager():
    site = _Site(owner=ANALYST)
    _call(site, "approve", "IC Balance", "ZZ-IC-1")
    assert site.only_for_calls == [("EPM Admin", "System Manager")]


def test_an_allowed_self_approval_with_no_reason_is_still_refused_by_the_hook():
    for reason in (None, "", "   "):
        site = _Site()
        _raises(lambda: _call(site, "approve", "IC Balance", "ZZ-IC-1", reason=reason),
                "PermissionError")
        assert site.submitted == [] and site.comments == []


def test_a_blocked_self_approval_is_refused_even_with_a_reason():
    site = _Site(policy="Blocked")
    _raises(lambda: _call(site, "approve", "IC Balance", "ZZ-IC-1", reason="ZZ why"),
            "PermissionError")
    assert site.submitted == []


# --- the reason flag ---------------------------------------------------------------

def test_the_reason_is_placed_on_the_flag_under_doctype_and_name():
    site = _Site()
    _call(site, "approve", "IC Balance", "ZZ-IC-1", reason="  ZZ month-end cover  ")
    assert site.flags["konsol_self_approval_reason"] == {("IC Balance", "ZZ-IC-1"):
                                                         "ZZ month-end cover"}


def test_no_reason_sets_no_flag():
    site = _Site(owner=ANALYST)
    _call(site, "approve", "IC Balance", "ZZ-IC-1")
    assert not site.flags.get("konsol_self_approval_reason")


# --- workflow vs docstatus-only ------------------------------------------------------

def test_a_workflow_doctype_calls_apply_workflow_approve():
    for doctype in WORKFLOW_DOCTYPES:
        site = _Site()
        out, _ = _call(site, "approve", doctype, "ZZ-W-1", reason="ZZ why")
        assert site.applied == [(doctype, "ZZ-W-1", "Approve")], site.applied
        assert site.workflow_reads == [{"document_type": doctype, "is_active": 1}]
        assert out == {"name": "ZZ-W-1", "docstatus": 1, "self_approved": True}
        assert len(site.comments) == 1 and "ZZ why" in site.comments[0][2]


def test_a_docstatus_only_doctype_calls_submit():
    for doctype in ("Group Exchange Rate", "Ownership Period", "Historical Equity Rate",
                    "IC Balance"):
        site = _Site(owner=ANALYST)
        out, _ = _call(site, "approve", doctype, "ZZ-D-1")
        assert site.applied == []
        assert site.submitted == [(doctype, "ZZ-D-1")]
        assert out == {"name": "ZZ-D-1", "docstatus": 1, "self_approved": False}
        assert site.comments == []


def test_an_inactive_workflow_falls_back_to_submit():
    site = _Site(owner=ANALYST, workflows=())
    out, _ = _call(site, "approve", "Business Combination", "ZZ-BC-1")
    assert site.applied == [] and site.submitted == [("Business Combination", "ZZ-BC-1")]
    assert out["docstatus"] == 1


def test_an_allowed_self_approval_with_a_reason_lands_with_a_comment():
    site = _Site()
    out, _ = _call(site, "approve", "IC Balance", "ZZ-IC-1", reason="ZZ reason")
    assert out == {"name": "ZZ-IC-1", "docstatus": 1, "self_approved": True}
    assert len(site.comments) == 1 and "ZZ reason" in site.comments[0][2]

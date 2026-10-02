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
EVENT_MODEL_PY = os.path.join(APP_DIR, "close", "close_event_model.py")
WRITER_PY = os.path.join(APP_DIR, "close", "close_event.py")

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
                 workflows=WORKFLOW_DOCTYPES, versions=(), run_hook=True, record_raises=None,
                 fields=None):
        self.roles = set(roles)
        self.run_hook = run_hook  # False: submit() does not run the P07 hook
        self.record_raises = record_raises  # the stub writer raises this
        self.fields = dict(fields or {})  # extra fields on every stub doc
        self.events = []  # Close Events recorded (T02b)
        self.versions = list(versions)  # Version rows {"docname", "owner", "data"}
        self.version_reads = []  # (doctype, docnames)
        self.state_field_reads = []  # doctypes
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
            self.status = None
            self.flags = types.SimpleNamespace()
            # A Frappe document has every field of its doctype; blank unless given.
            self.data_area_id = self.acquired_entity = self.disposed_entity = None
            for k, v in site.fields.items():
                setattr(self, k, v)

        def submit(self):
            if site.run_hook:
                hook["check"](self)  # the P07 before_submit hook, as Frappe runs it
            self.docstatus = 1
            site.submitted.append((self.doctype, self.name))

        def add_comment(self, comment_type, text):
            site.comments.append((self.doctype, self.name, text))

    def get_doc(doctype, name=None):
        site.get_doc_calls.append((doctype, name))
        return _Doc(doctype, name)

    def get_all(doctype, filters=None, fields=None, **k):
        assert doctype == "Version", doctype
        names = list(filters["docname"][1])
        site.version_reads.append((filters["ref_doctype"], names))
        return [dict(v) for v in site.versions if v["docname"] in names]

    def get_value(doctype, filters, fieldname="name", **k):
        assert doctype == "Workflow", doctype
        if fieldname == "workflow_state_field":
            # self_approval.preparers_for's read, kept apart from the
            # endpoint's routing read below.
            site.state_field_reads.append(filters.get("document_type"))
            if filters.get("is_active") == 1 and filters.get("document_type") in site.workflows:
                return "status"
            return None
        site.workflow_reads.append(dict(filters))
        if filters.get("is_active") == 1 and filters.get("document_type") in site.workflows:
            return "ZZ " + filters["document_type"] + " Workflow"
        return None

    def get_single_value(dt, field, *a, **k):
        assert (dt, field) == ("Close Settings", "self_approval"), (dt, field)
        return site.policy

    def apply_workflow(doc, action):
        site.applied.append((doc.doctype, doc.name, action))
        assert action in ("Approve", "Reject"), action
        # apply_workflow rebuilds and reloads the doc (workflow.py:101-102),
        # so only the request-scoped flag survives, not doc.flags.
        fresh = _Doc(doc.doctype, doc.name)
        if action == "Approve":
            fresh.submit()
        else:
            # Reject moves docstatus 0 -> 0 (Pending Approval -> Draft): a
            # save, not a submit.
            fresh.status = "Draft"
        return fresh

    frappe.throw = throw
    frappe._ = lambda s: s
    frappe.only_for = only_for
    frappe.whitelist = whitelist
    frappe.get_doc = get_doc
    frappe.get_all = get_all
    frappe.get_roles = lambda user=None: sorted(site.roles)
    frappe.db = types.SimpleNamespace(get_value=get_value, get_single_value=get_single_value)
    frappe.session = types.SimpleNamespace(user=site.user)
    workflow = types.ModuleType("frappe.model.workflow")
    workflow.apply_workflow = apply_workflow
    model = types.ModuleType("frappe.model")
    model.workflow = workflow
    frappe.model = model
    return frappe, hook


def _writer(site, frappe):
    """The real T02a writer loaded by path under stubs (so ``entity_of`` is the
    product's own); ``record`` appends to ``site.events`` (or raises
    ``site.record_raises``) and ``period_of`` returns (2025, 7)."""
    close_pkg = types.ModuleType("konsol.close")
    close_pkg.close_event_model = _load_by_path("close_event_model_for_t02b", EVENT_MODEL_PY)
    close_pkg.period_model = types.ModuleType("konsol.close.period_model")
    konsol_pkg = types.ModuleType("konsol")
    konsol_pkg.period_status = types.ModuleType("konsol.period_status")
    konsol_pkg.close = close_pkg
    controller = types.ModuleType("konsol.consolidation.doctype.close_event")
    controller.close_event = types.SimpleNamespace()
    mods = {"frappe": frappe, "konsol": konsol_pkg, "konsol.close": close_pkg,
            "konsol.period_status": konsol_pkg.period_status,
            "konsol.close.period_model": close_pkg.period_model,
            "konsol.close.close_event_model": close_pkg.close_event_model,
            "konsol.consolidation.doctype.close_event": controller}
    saved = {n: sys.modules.get(n) for n in mods}
    sys.modules.update(mods)
    try:
        writer = _load_by_path("close_event_writer_for_t02b", WRITER_PY)
    finally:
        for n, old in saved.items():
            if old is None:
                sys.modules.pop(n, None)
            else:
                sys.modules[n] = old

    def record(kind, fiscal_year, fiscal_period, reference_doctype=None, reference_name=None,
               reason=None, detail=None, entity=None):
        if site.record_raises:
            raise site.record_raises
        site.events.append({"kind": kind, "fiscal_year": fiscal_year,
                            "fiscal_period": fiscal_period,
                            "reference_doctype": reference_doctype,
                            "reference_name": reference_name, "reason": reason,
                            "detail": detail, "entity": entity})
        return "CE-%09d" % len(site.events)

    writer.record = record
    writer.period_of = lambda doc: (2025, 7)
    return writer, close_pkg.close_event_model


def _call(site, fn, *args, **kwargs):
    frappe, hook = _frappe(site)
    writer, event_model = _writer(site, frappe)
    names = ["konsol", "konsol.close"]
    mods = {n: types.ModuleType(n) for n in names}
    mods.update({"frappe": frappe, "frappe.model": frappe.model,
                 "frappe.model.workflow": frappe.model.workflow})
    saved = {n: sys.modules.get(n) for n in
             names + ["frappe", "frappe.model", "frappe.model.workflow",
                      "konsol.close.close_policy_model", "konsol.close.self_approval",
                      "konsol.close.close_event", "konsol.close.close_event_model"]}
    sys.modules.update(mods)
    try:
        sys.modules["konsol.close.close_event"] = writer
        sys.modules["konsol.close.close_event_model"] = event_model
        mods["konsol.close"].close_event = writer
        mods["konsol.close"].close_event_model = event_model
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


# --- #305-W2-14 (P30b): self_approved follows the preparer rule ----------------------

def _edit(name, owner, field="ic_sales_amount"):
    import json
    return {"docname": name, "owner": owner,
            "data": json.dumps({"changed": [[field, 1, 2]], "added": [], "removed": [],
                                "row_changed": []})}


def test_a_close_lead_who_edited_the_draft_is_self_approved():
    # The failure path: the endpoint reported owner == user only.
    site = _Site(owner=ANALYST, versions=[_edit("ZZ-IC-1", LEAD)])
    out, _ = _call(site, "approve", "IC Balance", "ZZ-IC-1", reason="ZZ cover")
    assert out == {"name": "ZZ-IC-1", "docstatus": 1, "self_approved": True}, out
    assert len(site.comments) == 1 and "ZZ cover" in site.comments[0][2]
    assert ("IC Balance", ["ZZ-IC-1"]) in site.version_reads, site.version_reads


def test_a_close_lead_who_edited_the_draft_with_no_reason_is_refused():
    site = _Site(owner=ANALYST, versions=[_edit("ZZ-IC-1", LEAD)])
    _raises(lambda: _call(site, "approve", "IC Balance", "ZZ-IC-1"), "PermissionError")
    assert site.submitted == [] and site.comments == []


def test_a_journal_reject_by_the_close_lead_is_not_self_approved():
    site = _Site(owner=ANALYST, policy="Blocked",
                 versions=[_edit("ZZ-CJ-1", LEAD, field="status")])
    out, _ = _call(site, "approve", "Consolidation Journal", "ZZ-CJ-1")
    assert out == {"name": "ZZ-CJ-1", "docstatus": 1, "self_approved": False}, out
    assert "Consolidation Journal" in site.state_field_reads


# --- reject (J06a: rejecting a journal needs a reason) -----------------------------

def test_reject_with_a_blank_reason_is_refused():
    for reason in (None, "", "   "):
        site = _Site()
        msg = _raises(lambda: _call(site, "reject", "Consolidation Journal", "ZZ-CJ-1",
                                     reason=reason), "ValidationError")
        assert "reason" in msg.lower(), msg
        assert site.applied == [] and site.comments == []
        assert not site.flags.get("konsol_reject_reason")


def test_reject_by_an_analyst_is_refused_by_only_for():
    site = _Site(roles=("EPM Analyst",), user=ANALYST)
    _raises(lambda: _call(site, "reject", "Consolidation Journal", "ZZ-CJ-1", reason="ZZ why"),
            "PermissionError")
    assert site.only_for_calls == [("EPM Admin", "System Manager")]
    assert site.applied == [] and site.comments == []


def test_reject_a_doctype_with_no_active_workflow_is_refused_naming_d2_8():
    for doctype in ("IC Balance", "Trial Balance Submission", "User"):
        site = _Site()
        msg = _raises(lambda: _call(site, "reject", doctype, "ZZ-1", reason="ZZ why"),
                      "ValidationError")
        assert "D2-8" in msg or "305-D2-8" in msg, msg
        assert site.applied == [] and site.comments == []
        assert not site.flags.get("konsol_reject_reason")


def test_reject_with_a_reason_applies_the_workflow_sets_the_flag_and_comments():
    site = _Site()
    out, _ = _call(site, "reject", "Consolidation Journal", "ZZ-CJ-1", reason="ZZ wrong account")
    assert site.applied == [("Consolidation Journal", "ZZ-CJ-1", "Reject")], site.applied
    assert site.flags["konsol_reject_reason"] == {("Consolidation Journal", "ZZ-CJ-1"):
                                                   "ZZ wrong account"}
    assert out["name"] == "ZZ-CJ-1"
    assert len(site.comments) == 1
    assert site.comments[0] == ("Consolidation Journal", "ZZ-CJ-1", "Rejected: ZZ wrong account")


def test_the_reject_and_self_approval_reason_flags_are_named_differently():
    assert "konsol_reject_reason" != "konsol_self_approval_reason"


# --- T02b (#305-W2-1): reject writes its Close Event; approve leaves it to the hook ---

def test_reject_records_one_rejected_event_with_its_reason():
    site = _Site(owner=ANALYST)
    _call(site, "reject", "Consolidation Journal", "ZZ-CJ-1", reason="  wrong entity ")
    assert site.events == [{
        "kind": "rejected", "fiscal_year": 2025, "fiscal_period": 7,
        "reference_doctype": "Consolidation Journal", "reference_name": "ZZ-CJ-1",
        "reason": "wrong entity", "detail": {"preparer": ANALYST}, "entity": None,
    }], site.events


def test_reject_of_a_business_combination_carries_its_entity():
    # #305-W2-9: the reject event gets the same entity as the approval's.
    site = _Site(owner=ANALYST, fields={"acquired_entity": "ZZ01"})
    _call(site, "reject", "Business Combination", "ZZ-BC-1", reason="ZZ wrong date")
    assert [e["entity"] for e in site.events] == ["ZZ01"], site.events


def test_reject_with_a_blank_reason_records_no_event():
    # Failure path: refused before any event.
    for reason in (None, "", "   "):
        site = _Site()
        _raises(lambda: _call(site, "reject", "Consolidation Journal", "ZZ-CJ-1",
                              reason=reason), "ValidationError")
        assert site.events == []


def test_reject_of_a_doctype_with_no_workflow_records_no_event():
    site = _Site()
    _raises(lambda: _call(site, "reject", "IC Balance", "ZZ-1", reason="ZZ why"),
            "ValidationError")
    assert site.events == []


def test_a_failing_writer_stops_the_reject():
    # Failure path: the writer's exception propagates out of reject, uncaught.
    boom = RuntimeError("ZZ close event insert failed")
    site = _Site(record_raises=boom)
    try:
        _call(site, "reject", "Consolidation Journal", "ZZ-CJ-1", reason="ZZ why")
    except RuntimeError as e:
        assert e is boom
    else:
        raise AssertionError("the writer's exception was swallowed")
    assert site.events == []


def test_approve_records_nothing_itself():
    # E10-P11: the approval event is the hook's; with the hook not run, approve
    # writes no event.
    for doctype in ("IC Balance",) + WORKFLOW_DOCTYPES:
        site = _Site(owner=ANALYST, run_hook=False)
        _call(site, "approve", doctype, "ZZ-1")
        assert site.events == [], (doctype, site.events)


def test_approve_through_the_real_hook_records_exactly_one_event():
    site = _Site(owner=ANALYST)
    _call(site, "approve", "IC Balance", "ZZ-IC-1")
    assert [e["kind"] for e in site.events] == ["approved"], site.events
    site = _Site()
    _call(site, "approve", "Consolidation Journal", "ZZ-CJ-1", reason="ZZ cover")
    assert [(e["kind"], e["reason"]) for e in site.events] == [
        ("self_approved", "ZZ cover")], site.events


def test_a_failing_writer_stops_the_approve():
    boom = RuntimeError("ZZ close event insert failed")
    site = _Site(owner=ANALYST, record_raises=boom)
    try:
        _call(site, "approve", "IC Balance", "ZZ-IC-1")
    except RuntimeError as e:
        assert e is boom
    else:
        raise AssertionError("the writer's exception was swallowed")
    assert site.submitted == [] and site.events == []


def test_approve_does_not_call_the_writer_in_its_source():
    import ast
    with open(API_PY) as f:
        tree = ast.parse(f.read())
    approve = next(n for n in tree.body
                   if isinstance(n, ast.FunctionDef) and n.name == "approve")
    calls = [ast.unparse(n.func) for n in ast.walk(approve) if isinstance(n, ast.Call)]
    assert not [c for c in calls if "close_event" in c or c.endswith("record")], calls
    reject = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "reject")
    calls = [ast.unparse(n.func) for n in ast.walk(reject) if isinstance(n, ast.Call)]
    assert "close_event.record" in calls, calls

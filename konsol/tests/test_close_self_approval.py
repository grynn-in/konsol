"""konsol#305 P07 — the self-approval hook on every approval doctype (R5,
#305-D2-3).

`konsol.close.self_approval.check` runs as `doc_events["*"]["before_submit"]`.
It acts only on `close_policy_model.APPROVAL_DOCTYPES` and applies
`close_policy_model.self_approval_problem`. There is no Administrator
exemption (coordinator, 27 Sep): Administrator approving their own draft is
refused like anyone else.

The module is loaded by path against a stub frappe (the pattern of
test_assertion_warn_amber.py:58-133); the pure model is loaded for real.
"""
import ast
import glob
import importlib.util
import json
import os
import sys
import types

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOOK_PY = os.path.join(APP_DIR, "close", "self_approval.py")
MODEL_PY = os.path.join(APP_DIR, "close", "close_policy_model.py")
HOOKS = os.path.join(APP_DIR, "hooks.py")

ADMIN = "lead@example.com"
OTHER = "analyst@example.com"


class _Flags(dict):
    def __getattr__(self, n):
        return self.get(n)

    def __setattr__(self, n, v):
        self[n] = v


def _model():
    spec = importlib.util.spec_from_file_location("close_policy_model_for_p07", MODEL_PY)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _load(policy, user=ADMIN, flags=None, versions=None, state_fields=None):
    """``versions``: Version rows as ``{"docname", "owner", "data"}`` (the
    site's Version table). ``state_fields``: ``{doctype: workflow_state_field}``
    for the doctypes with an active workflow. Every ``get_all`` and
    ``db.get_value`` call is recorded in ``reads`` too."""
    versions = list(versions or ())
    state_fields = dict(state_fields or {})
    frappe = types.ModuleType("frappe")
    frappe.ValidationError = type("ValidationError", (Exception,), {})
    frappe.PermissionError = type("PermissionError", (Exception,), {})

    def throw(msg, exc=None, **k):
        raise (exc or frappe.ValidationError)(msg)

    reads = []

    def get_single_value(dt, field, *a, **k):
        reads.append((dt, field))
        assert (dt, field) == ("Close Settings", "self_approval"), (dt, field)
        return policy

    def get_all(doctype, filters=None, fields=None, order_by=None, limit_page_length=None,
                **k):
        reads.append(("get_all", doctype, dict(filters or {}), tuple(fields or ()),
                      order_by, limit_page_length))
        assert doctype == "Version", doctype
        assert filters["docname"][0] == "in", filters
        names = set(filters["docname"][1])
        return [dict(v) for v in versions if v["docname"] in names]

    def get_value(doctype, filters=None, fieldname="name", *a, **k):
        reads.append(("get_value", doctype, dict(filters or {}), fieldname))
        assert doctype == "Workflow" and fieldname == "workflow_state_field", (doctype, fieldname)
        if filters.get("is_active") != 1:
            return None
        return state_fields.get(filters.get("document_type"))

    frappe.throw = throw
    frappe.flags = _Flags(flags or {})
    frappe.session = types.SimpleNamespace(user=user)
    frappe.get_all = get_all
    frappe.db = types.SimpleNamespace(get_single_value=get_single_value, get_value=get_value)

    close_pkg = types.ModuleType("konsol.close")
    close_pkg.__path__ = [os.path.join(APP_DIR, "close")]
    model = _model()
    close_pkg.close_policy_model = model
    konsol_pkg = types.ModuleType("konsol")
    konsol_pkg.__path__ = [APP_DIR]
    konsol_pkg.close = close_pkg

    mods = {"frappe": frappe, "konsol": konsol_pkg, "konsol.close": close_pkg,
            "konsol.close.close_policy_model": model}
    saved = {n: sys.modules.get(n) for n in mods}
    sys.modules.update(mods)
    try:
        spec = importlib.util.spec_from_file_location("self_approval_under_test", HOOK_PY)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        for n, old in saved.items():
            if old is not None:
                sys.modules[n] = old
            else:
                sys.modules.pop(n, None)
    return module, frappe, reads


def _doc(doctype="IC Balance", name="ICB-1", owner=ADMIN):
    comments = []
    doc = types.SimpleNamespace(doctype=doctype, name=name, owner=owner, comments=comments)
    doc.add_comment = lambda kind, text=None, **k: comments.append((kind, text))
    return doc


def _raises(fn, exc):
    try:
        fn()
    except exc as e:
        return str(e)
    raise AssertionError("expected %s" % exc.__name__)


def test_a_doctype_outside_the_list_is_untouched_even_when_self_approved():
    for dt in ("Trial Balance Submission", "TB Exception", "Budget Cycle", "ToDo"):
        mod, frappe, reads = _load(policy="Blocked")
        doc = _doc(doctype=dt)
        mod.check(doc, "before_submit")
        assert doc.comments == []
        assert reads == [], "the policy is read only for approval doctypes"


def test_blocked_self_approval_raises_and_comments_nothing():
    # The failure path: a Close Lead approving their own draft under Blocked.
    for dt in _model().APPROVAL_DOCTYPES:
        mod, frappe, _ = _load(policy="Blocked")
        doc = _doc(doctype=dt, name="X-1")
        msg = _raises(lambda: mod.check(doc, "before_submit"), frappe.PermissionError)
        assert "Close Settings blocks self-approval" in msg, msg
        assert "X-1" in msg and dt in msg
        assert doc.comments == []


def test_blocked_with_a_reason_still_raises():
    mod, frappe, _ = _load(policy="Blocked", flags={
        "konsol_self_approval_reason": {("IC Balance", "ICB-1"): "because"}})
    doc = _doc()
    _raises(lambda: mod.check(doc, "before_submit"), frappe.PermissionError)
    assert doc.comments == []


def test_administrator_is_not_exempt():
    # Coordinator, 27 Sep: no Administrator exemption.
    mod, frappe, _ = _load(policy="Blocked", user="Administrator")
    doc = _doc(owner="Administrator")
    _raises(lambda: mod.check(doc, "before_submit"), frappe.PermissionError)
    mod, frappe, _ = _load(policy="", user="Administrator")
    _raises(lambda: mod.check(_doc(owner="Administrator"), "before_submit"),
            frappe.PermissionError)


def test_someone_elses_draft_passes_under_every_policy():
    for policy in ("", None, "Blocked", "Allowed with reason"):
        mod, frappe, _ = _load(policy=policy, user=ADMIN)
        doc = _doc(owner=OTHER)
        mod.check(doc, "before_submit")
        assert doc.comments == []


def test_undeclared_self_approval_raises_with_the_close_settings_message():
    for policy in ("", None):
        mod, frappe, _ = _load(policy=policy)
        doc = _doc()
        msg = _raises(lambda: mod.check(doc, "before_submit"), frappe.PermissionError)
        assert "Declare the self-approval policy" in msg and "Close Settings" in msg, msg
        assert msg.startswith("%s prepared IC Balance ICB-1; " % ADMIN), msg
        assert doc.comments == []


def test_allowed_with_a_flag_reason_passes_and_comments_once():
    mod, frappe, _ = _load(policy="Allowed with reason", flags={
        "konsol_self_approval_reason": {("IC Balance", "ICB-1"): "ZZ reason"}})
    doc = _doc()
    mod.check(doc, "before_submit")
    assert len(doc.comments) == 1, doc.comments
    kind, text = doc.comments[0]
    assert kind == "Comment"
    assert text == ("Self-approved by %s under Close Settings (Allowed with reason): ZZ reason"
                    % ADMIN), text


def test_a_reason_for_another_document_does_not_count():
    mod, frappe, _ = _load(policy="Allowed with reason", flags={
        "konsol_self_approval_reason": {("IC Balance", "ICB-2"): "ZZ reason"}})
    doc = _doc()
    _raises(lambda: mod.check(doc, "before_submit"), frappe.PermissionError)
    assert doc.comments == []


def test_allowed_with_no_reason_raises():
    for flags in ({}, {"konsol_self_approval_reason": {("IC Balance", "ICB-1"): "   "}}):
        mod, frappe, _ = _load(policy="Allowed with reason", flags=flags)
        doc = _doc()
        msg = _raises(lambda: mod.check(doc, "before_submit"), frappe.PermissionError)
        assert "konsol.close.approval_api.approve" in msg, msg
        assert doc.comments == []


def test_ownership_period_from_a_business_combination_passes_under_blocked():
    mod, frappe, _ = _load(policy="Blocked", flags={"from_business_combination": True})
    doc = _doc(doctype="Ownership Period", name="OP-1")
    mod.check(doc, "before_submit")
    assert doc.comments == []


def test_the_business_combination_flag_does_not_exempt_other_doctypes():
    mod, frappe, _ = _load(policy="Blocked", flags={"from_business_combination": True})
    _raises(lambda: mod.check(_doc(doctype="IC Balance"), "before_submit"),
            frappe.PermissionError)


def test_patch_install_and_migrate_pass():
    for flag in ("in_patch", "in_install", "in_migrate"):
        mod, frappe, _ = _load(policy="Blocked", flags={flag: True})
        doc = _doc()
        mod.check(doc, "before_submit")
        assert doc.comments == []


# --- #305-W2-14 (P30b): a preparer is the owner, or anyone who edited the draft ---

def _version(name, owner, changed=None, **data):
    d = {"changed": changed or [], "added": [], "removed": [], "row_changed": []}
    d.update(data)
    return {"docname": name, "owner": owner, "data": json.dumps(d)}


def test_the_close_lead_who_edited_an_analysts_draft_is_blocked():
    # The failure path, the W2-14 case: red while ``user != owner`` returns early.
    mod, frappe, _ = _load(policy="Blocked", versions=[
        _version("ICB-1", ADMIN, [["ic_sales_amount", 100, 120]])])
    doc = _doc(owner=OTHER)
    msg = _raises(lambda: mod.check(doc, "before_submit"), frappe.PermissionError)
    assert "blocks self-approval" in msg, msg
    assert doc.comments == []


def test_the_editor_approver_passes_with_a_flag_reason_under_allowed():
    mod, frappe, _ = _load(policy="Allowed with reason", versions=[
        _version("ICB-1", ADMIN, [["ic_sales_amount", 100, 120]])], flags={
        "konsol_self_approval_reason": {("IC Balance", "ICB-1"): "ZZ reason"}})
    doc = _doc(owner=OTHER)
    mod.check(doc, "before_submit")
    assert len(doc.comments) == 1, doc.comments
    assert doc.comments[0] == (
        "Comment",
        "Self-approved by %s under Close Settings (Allowed with reason): ZZ reason" % ADMIN)


def test_the_editor_approver_with_no_reason_is_refused_under_allowed():
    mod, frappe, _ = _load(policy="Allowed with reason", versions=[
        _version("ICB-1", ADMIN, [["ic_sales_amount", 100, 120]])])
    doc = _doc(owner=OTHER)
    _raises(lambda: mod.check(doc, "before_submit"), frappe.PermissionError)
    assert doc.comments == []


def test_a_journal_reject_alone_does_not_make_the_close_lead_a_preparer():
    mod, frappe, reads = _load(policy="Blocked", versions=[
        _version("CJ-1", ADMIN, [["status", "Pending Approval", "Draft"]])],
        state_fields={"Consolidation Journal": "status"})
    doc = _doc(doctype="Consolidation Journal", name="CJ-1", owner=OTHER)
    mod.check(doc, "before_submit")
    assert doc.comments == []
    assert ("get_value", "Workflow", {"document_type": "Consolidation Journal", "is_active": 1},
            "workflow_state_field") in reads, reads


def test_a_journal_line_edit_makes_the_close_lead_a_preparer():
    mod, frappe, _ = _load(policy="Blocked", versions=[
        _version("CJ-1", ADMIN, row_changed=[["lines", 0, "row-1", [["debit", 1, 2]]]])],
        state_fields={"Consolidation Journal": "status"})
    doc = _doc(doctype="Consolidation Journal", name="CJ-1", owner=OTHER)
    _raises(lambda: mod.check(doc, "before_submit"), frappe.PermissionError)


def test_another_documents_versions_do_not_count():
    mod, frappe, _ = _load(policy="Blocked", versions=[
        _version("ICB-2", ADMIN, [["ic_sales_amount", 100, 120]])])
    doc = _doc(owner=OTHER)
    mod.check(doc, "before_submit")
    assert doc.comments == []


def test_a_doctype_outside_the_list_reads_no_versions():
    # Failure path: reading Versions on every submit of every doctype.
    for dt in ("Trial Balance Submission", "ToDo"):
        mod, frappe, reads = _load(policy="Blocked", versions=[
            _version("ICB-1", ADMIN, [["x", 1, 2]])])
        mod.check(_doc(doctype=dt, owner=OTHER), "before_submit")
        assert [r for r in reads if r[0] in ("get_all", "get_value")] == [], reads


def test_preparers_for_makes_two_reads_for_any_number_of_names():
    mod, frappe, reads = _load(policy="Blocked", versions=[
        _version("OP-1", ADMIN, [["ownership_pct", 50, 60]]),
        _version("OP-2", OTHER, [["docstatus", 0, 1]]),
        _version("OP-9", ADMIN, [["ownership_pct", 1, 2]])])
    out = mod.preparers_for("Ownership Period", {"OP-1": OTHER, "OP-2": OTHER, "OP-3": ADMIN})
    assert out == {"OP-1": frozenset((OTHER, ADMIN)), "OP-2": frozenset((OTHER,)),
                   "OP-3": frozenset((ADMIN,))}, out
    gets = [r for r in reads if r[0] == "get_all"]
    assert gets == [("get_all", "Version",
                     {"ref_doctype": "Ownership Period",
                      "docname": ["in", ["OP-1", "OP-2", "OP-3"]]},
                     ("docname", "owner", "data"), "creation asc", 0)], gets
    assert [r for r in reads if r[0] == "get_value"] == [
        ("get_value", "Workflow", {"document_type": "Ownership Period", "is_active": 1},
         "workflow_state_field")], reads


def test_preparers_for_no_names_reads_nothing():
    mod, frappe, reads = _load(policy="Blocked")
    assert mod.preparers_for("IC Balance", {}) == {}
    assert reads == []


# --- hooks.py ---------------------------------------------------------------

def test_hooks_wire_the_check_as_the_star_before_submit():
    with open(HOOKS) as f:
        tree = ast.parse(f.read())
    found = []
    for n in tree.body:
        if not isinstance(n, ast.Assign):
            continue
        for t in n.targets:
            if (isinstance(t, ast.Subscript) and getattr(t.value, "id", None) == "doc_events"
                    and ast.literal_eval(t.slice) == "*"):
                found.append(ast.literal_eval(n.value))
    assert found == [{"before_submit": "konsol.close.self_approval.check"}], found


# --- workflows --------------------------------------------------------------

def test_every_transition_into_a_submitted_state_allows_self_approval():
    # Frappe's has_approval_access (workflow.py:221-222) runs before our hook;
    # 0 would refuse even under "Allowed with reason", so it must stay 1 and
    # the hook stays the one rule.
    paths = glob.glob(os.path.join(APP_DIR, "**", "*_workflow.json"), recursive=True)
    assert paths, "no workflow JSON found"
    checked = 0
    for p in paths:
        wf = json.load(open(p))
        docstatus = {s["state"]: int(s.get("doc_status") or 0) for s in wf["states"]}
        for t in wf["transitions"]:
            if docstatus.get(t["next_state"]) == 1:
                checked += 1
                assert t.get("allow_self_approval") == 1, (p, t)
    assert checked >= 3, checked


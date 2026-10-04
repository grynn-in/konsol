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
EVENT_MODEL_PY = os.path.join(APP_DIR, "close", "close_event_model.py")
WRITER_PY = os.path.join(APP_DIR, "close", "close_event.py")
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


def _by_path(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _writer(frappe, events, record_raises=None, period_raises=None):
    """The real T02a writer (``konsol/close/close_event.py``), loaded by path
    under stubs, so ``entity_of`` is the product's own. ``record`` appends the
    event to ``events`` instead of inserting; ``period_of`` returns (2025, 7)
    or raises ``period_raises``."""
    period_status = types.ModuleType("konsol.period_status")
    period_model = types.ModuleType("konsol.close.period_model")
    controller = types.ModuleType("konsol.consolidation.doctype.close_event")
    controller.close_event = types.SimpleNamespace()
    close_pkg = types.ModuleType("konsol.close")
    close_pkg.close_event_model = _by_path("close_event_model_for_t02b", EVENT_MODEL_PY)
    close_pkg.period_model = period_model
    konsol_pkg = types.ModuleType("konsol")
    konsol_pkg.period_status = period_status
    konsol_pkg.close = close_pkg
    mods = {"frappe": frappe, "konsol": konsol_pkg, "konsol.close": close_pkg,
            "konsol.period_status": period_status,
            "konsol.close.period_model": period_model,
            "konsol.close.close_event_model": close_pkg.close_event_model,
            "konsol.consolidation.doctype.close_event": controller}
    saved = {n: sys.modules.get(n) for n in mods}
    sys.modules.update(mods)
    try:
        writer = _by_path("close_event_writer_for_t02b", WRITER_PY)
    finally:
        for n, old in saved.items():
            if old is not None:
                sys.modules[n] = old
            else:
                sys.modules.pop(n, None)

    def record(kind, fiscal_year, fiscal_period, reference_doctype=None, reference_name=None,
               reason=None, detail=None, entity=None):
        if record_raises:
            raise record_raises
        events.append({"kind": kind, "fiscal_year": fiscal_year,
                       "fiscal_period": fiscal_period, "reference_doctype": reference_doctype,
                       "reference_name": reference_name, "reason": reason,
                       "detail": detail, "entity": entity})
        return "CE-%09d" % len(events)

    def period_of(doc):
        if period_raises:
            raise period_raises
        return 2025, 7

    writer.record = record
    writer.period_of = period_of
    return writer


def _load(policy, user=ADMIN, flags=None, versions=None, state_fields=None,
          record_raises=None, period_raises=None, table_exists=True):
    """``versions``: Version rows as ``{"docname", "owner", "data"}`` (the
    site's Version table). ``state_fields``: ``{doctype: workflow_state_field}``
    for the doctypes with an active workflow. Every ``get_all`` and
    ``db.get_value`` call is recorded in ``reads`` too. The Close Events the
    hook records are in ``frappe.events`` (T02b)."""
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

    def table_exists_fn(doctype):
        # Not tracked in reads: the self-approval judgment reads (Version,
        # Workflow) are what "a system submit reads nothing" pins; the table
        # guard is a separate, cheap check made only on the system path.
        assert doctype == "Close Event", doctype
        return table_exists

    frappe.throw = throw
    frappe.flags = _Flags(flags or {})
    frappe.session = types.SimpleNamespace(user=user)
    frappe.get_all = get_all
    frappe.db = types.SimpleNamespace(get_single_value=get_single_value, get_value=get_value,
                                      table_exists=table_exists_fn)
    frappe.logger = lambda: types.SimpleNamespace(
        warning=lambda *a, **k: None, info=lambda *a, **k: None)

    frappe.events = []
    writer = _writer(frappe, frappe.events, record_raises, period_raises)

    # R01a: Frappe's own diff of the submitting save (version.get_diff),
    # stubbed over the test docs' ``values``: scalar fields go to ``changed``,
    # list fields are child rows keyed by ``name``. Calls are in ``frappe.diffs``.
    frappe.diffs = []

    def get_diff(old, new, for_child=False, compare_cancelled=False):
        frappe.diffs.append((old, new))
        out = {"changed": [], "added": [], "removed": [], "row_changed": [],
               "data_import": None, "updater_reference": None}
        for key in sorted(set(old.values) | set(new.values)):
            o, n = old.values.get(key), new.values.get(key)
            if isinstance(o, list) or isinstance(n, list):
                old_rows = {r["name"]: r for r in o or ()}
                new_rows = {r["name"]: r for r in n or ()}
                for i, (rn, row) in enumerate(new_rows.items()):
                    if rn not in old_rows:
                        out["added"].append([key, row])
                    elif row != old_rows[rn]:
                        out["row_changed"].append([key, i, rn, [
                            [f, old_rows[rn].get(f), row.get(f)] for f in sorted(row)
                            if row.get(f) != old_rows[rn].get(f)]])
                out["removed"].extend([key, r] for rn, r in old_rows.items()
                                      if rn not in new_rows)
            elif o != n:
                out["changed"].append((key, o, n))
        if any(out[k] for k in ("changed", "added", "removed", "row_changed")):
            return out
        return None

    version_mod = types.ModuleType("frappe.core.doctype.version.version")
    version_mod.get_diff = get_diff

    close_pkg = types.ModuleType("konsol.close")
    close_pkg.__path__ = [os.path.join(APP_DIR, "close")]
    model = _model()
    close_pkg.close_policy_model = model
    close_pkg.close_event = writer
    close_pkg.close_event_model = _by_path("close_event_model_for_t02b", EVENT_MODEL_PY)
    konsol_pkg = types.ModuleType("konsol")
    konsol_pkg.__path__ = [APP_DIR]
    konsol_pkg.close = close_pkg

    mods = {"frappe": frappe, "konsol": konsol_pkg, "konsol.close": close_pkg,
            "konsol.close.close_policy_model": model,
            "konsol.close.close_event": writer,
            "konsol.close.close_event_model": close_pkg.close_event_model,
            "frappe.core": types.ModuleType("frappe.core"),
            "frappe.core.doctype": types.ModuleType("frappe.core.doctype"),
            "frappe.core.doctype.version": types.ModuleType("frappe.core.doctype.version"),
            "frappe.core.doctype.version.version": version_mod}
    saved = {n: sys.modules.get(n) for n in mods}
    sys.modules.update(mods)
    try:
        spec = importlib.util.spec_from_file_location("self_approval_under_test", HOOK_PY)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        _restore(saved)

    real_check = module.check

    def check(doc, method=None):
        # check imports the writer at call time (T02b), so the stubs are in
        # sys.modules for the call too.
        saved_now = {n: sys.modules.get(n) for n in mods}
        sys.modules.update(mods)
        try:
            return real_check(doc, method)
        finally:
            _restore(saved_now)

    module.check = check
    return module, frappe, reads


def _restore(saved):
    for n, old in saved.items():
        if old is not None:
            sys.modules[n] = old
        else:
            sys.modules.pop(n, None)


def _doc(doctype="IC Balance", name="ICB-1", owner=ADMIN, before=None, values=None,
         **fields):
    """``before``: the saved draft's field values (``get_doc_before_save``),
    None for an insert-and-submit. ``values``: this submit's field values; a
    list value is a child table of rows keyed by ``name``. Both feed the stub
    ``get_diff``; docstatus is 0 before and 1 now, as at ``before_submit``."""
    comments = []
    # A Frappe document has every field of its doctype; blank unless given.
    fields = dict({"data_area_id": None, "acquired_entity": None, "disposed_entity": None},
                  **fields)
    doc = types.SimpleNamespace(doctype=doctype, name=name, owner=owner, comments=comments,
                                **fields)
    doc.add_comment = lambda kind, text=None, **k: comments.append((kind, text))
    doc.values = dict(values if values is not None else (before or {}), docstatus=1)
    saved = None
    if before is not None:
        saved = types.SimpleNamespace(doctype=doctype, name=name, owner=owner,
                                      values=dict(before, docstatus=0))
    doc.get_doc_before_save = lambda: saved
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


# --- R01a (#305-W2-14, review M1): edits carried in the submitting request ---
# The Version of the submitting save is written after before_submit
# (frappe/model/document.py:1197), so preparers_for cannot see it; the hook
# reads Frappe's diff of the pending save instead.

GER_DRAFT = {"quote": 1.1, "from_currency": "EUR"}


def test_the_close_lead_who_changes_the_quote_while_submitting_is_blocked():
    # Failure path, review M1: red while the hook reads only saved Versions.
    mod, frappe, _ = _load(policy="Blocked")
    doc = _doc(doctype="Group Exchange Rate", name="GER-1", owner=OTHER,
               before=GER_DRAFT, values=dict(GER_DRAFT, quote=1.5))
    msg = _raises(lambda: mod.check(doc, "before_submit"), frappe.PermissionError)
    assert "blocks self-approval" in msg and "GER-1" in msg, msg
    assert frappe.events == [] and doc.comments == []
    assert frappe.diffs and frappe.diffs[0][0] is doc.get_doc_before_save(), frappe.diffs
    assert frappe.diffs[0][1] is doc, frappe.diffs


def test_the_quote_changer_needs_a_reason_under_allowed_with_reason():
    mod, frappe, _ = _load(policy="Allowed with reason")
    doc = _doc(doctype="Group Exchange Rate", name="GER-1", owner=OTHER,
               before=GER_DRAFT, values=dict(GER_DRAFT, quote=1.5))
    msg = _raises(lambda: mod.check(doc, "before_submit"), frappe.PermissionError)
    assert "konsol.close.approval_api.approve" in msg, msg
    assert frappe.events == [] and doc.comments == []


def test_the_quote_changer_with_a_reason_is_recorded_as_self_approved_by_both():
    mod, frappe, _ = _load(policy="Allowed with reason", flags={
        "konsol_self_approval_reason": {("Group Exchange Rate", "GER-1"): "ZZ fix"}})
    doc = _doc(doctype="Group Exchange Rate", name="GER-1", owner=OTHER,
               before=GER_DRAFT, values=dict(GER_DRAFT, quote=1.5))
    mod.check(doc, "before_submit")
    assert len(doc.comments) == 1, doc.comments
    assert len(frappe.events) == 1, frappe.events
    event = frappe.events[0]
    assert event["kind"] == "self_approved" and event["reason"] == "ZZ fix", event
    assert event["detail"]["preparer"] == OTHER, event
    assert event["detail"]["preparers"] == sorted((ADMIN, OTHER)), event


def test_a_submit_that_changes_only_docstatus_is_an_approval():
    for policy in ("Blocked", "Allowed with reason", ""):
        mod, frappe, _ = _load(policy=policy)
        doc = _doc(doctype="Group Exchange Rate", name="GER-1", owner=OTHER,
                   before=GER_DRAFT)
        mod.check(doc, "before_submit")
        assert frappe.diffs, "the pending diff was not read"
        assert [e["kind"] for e in frappe.events] == ["approved"], frappe.events
        assert frappe.events[0]["detail"]["preparers"] == [OTHER], frappe.events


def test_a_workflow_approve_changes_only_the_state_field():
    mod, frappe, _ = _load(policy="Blocked", state_fields={"Consolidation Journal": "status"})
    draft = {"status": "Pending Approval", "lines": [{"name": "CJL-1", "debit": 1}]}
    doc = _doc(doctype="Consolidation Journal", name="CJ-1", owner=OTHER,
               before=draft, values=dict(draft, status="Approved"))
    mod.check(doc, "before_submit")
    assert [e["kind"] for e in frappe.events] == ["approved"], frappe.events


def test_a_journal_line_changed_while_approving_makes_the_close_lead_a_preparer():
    # Failure path: a child-row edit in the submitting request counts.
    mod, frappe, _ = _load(policy="Blocked", state_fields={"Consolidation Journal": "status"})
    draft = {"status": "Pending Approval", "lines": [{"name": "CJL-1", "debit": 1}]}
    for lines in ([{"name": "CJL-1", "debit": 2}],
                  [{"name": "CJL-1", "debit": 1}, {"name": "CJL-2", "debit": 0}],
                  []):
        doc = _doc(doctype="Consolidation Journal", name="CJ-1", owner=OTHER,
                   before=draft, values=dict(draft, status="Approved", lines=lines))
        _raises(lambda: mod.check(doc, "before_submit"), frappe.PermissionError)
    assert frappe.events == []


def test_an_insert_and_submit_has_no_saved_draft_to_diff():
    mod, frappe, _ = _load(policy="Blocked")
    doc = _doc(owner=OTHER, before=None)
    mod.check(doc, "before_submit")
    assert frappe.diffs == []
    assert [e["kind"] for e in frappe.events] == ["approved"], frappe.events


# --- T02b (#305-W2-1): every approval writes its Close Event in the hook ------

def test_someone_elses_ic_balance_records_one_approved_event():
    mod, frappe, _ = _load(policy="Blocked")
    mod.check(_doc(owner=OTHER), "before_submit")
    assert frappe.events == [{
        "kind": "approved", "fiscal_year": 2025, "fiscal_period": 7,
        "reference_doctype": "IC Balance", "reference_name": "ICB-1", "reason": None,
        "entity": None,
        "detail": {"preparer": OTHER, "preparers": [OTHER], "policy": None, "exempt": None},
    }], frappe.events


def test_an_allowed_self_approval_records_one_self_approved_event_with_its_reason():
    mod, frappe, _ = _load(policy="Allowed with reason", flags={
        "konsol_self_approval_reason": {("IC Balance", "ICB-1"): "ZZ reason"}})
    doc = _doc()
    mod.check(doc, "before_submit")
    assert len(doc.comments) == 1, doc.comments
    assert len(frappe.events) == 1, frappe.events
    event = frappe.events[0]
    assert event["kind"] == "self_approved", event
    assert event["reason"] == "ZZ reason", event
    assert event["detail"] == {"preparer": ADMIN, "preparers": [ADMIN],
                               "policy": "Allowed with reason", "exempt": None}, event


def test_the_close_lead_who_edited_the_analysts_draft_records_both_preparers():
    # #305-W2-14: the self-approved test is ``user in preparers``.
    mod, frappe, _ = _load(policy="Allowed with reason", versions=[
        _version("ICB-1", ADMIN, [["ic_sales_amount", 100, 120]])], flags={
        "konsol_self_approval_reason": {("IC Balance", "ICB-1"): "ZZ cover"}})
    mod.check(_doc(owner=OTHER), "before_submit")
    assert len(frappe.events) == 1, frappe.events
    event = frappe.events[0]
    assert event["kind"] == "self_approved" and event["reason"] == "ZZ cover", event
    assert event["detail"]["preparer"] == OTHER, event
    assert event["detail"]["preparers"] == sorted((ADMIN, OTHER)), event


def test_a_blocked_self_approval_records_zero_events():
    # Failure path: a refused approval writes nothing.
    for dt in _model().APPROVAL_DOCTYPES:
        mod, frappe, _ = _load(policy="Blocked")
        doc = _doc(doctype=dt, name="X-1")
        _raises(lambda: mod.check(doc, "before_submit"), frappe.PermissionError)
        assert frappe.events == [] and doc.comments == []


def test_an_undeclared_policy_self_approval_records_zero_events():
    for policy in ("", None):
        mod, frappe, _ = _load(policy=policy)
        _raises(lambda: mod.check(_doc(), "before_submit"), frappe.PermissionError)
        assert frappe.events == []


def test_an_allowed_self_approval_with_no_reason_records_zero_events():
    mod, frappe, _ = _load(policy="Allowed with reason")
    _raises(lambda: mod.check(_doc(), "before_submit"), frappe.PermissionError)
    assert frappe.events == []


def test_a_failing_writer_stops_the_approval():
    # Failure path: the writer's exception is never caught, so the submit
    # (and its transaction) fails with it.
    boom = RuntimeError("ZZ close event insert failed")
    for policy, owner, flags in (
            ("Blocked", OTHER, {}),
            ("Allowed with reason", ADMIN,
             {"konsol_self_approval_reason": {("IC Balance", "ICB-1"): "ZZ reason"}})):
        mod, frappe, _ = _load(policy=policy, flags=flags, record_raises=boom)
        try:
            mod.check(_doc(owner=owner), "before_submit")
        except RuntimeError as e:
            assert e is boom
        else:
            raise AssertionError("the writer's exception was swallowed")
        assert frappe.events == []


def test_a_record_with_no_declared_period_refuses_the_approval_with_no_event_or_comment():
    # #305-W2-5: period_of raising refuses the approval; nothing is recorded.
    no_period = ValueError("ZZ declare the period in EPM Fiscal Year first.")
    for policy, owner, flags in (
            ("Blocked", OTHER, {}),
            ("Allowed with reason", ADMIN,
             {"konsol_self_approval_reason": {("Ownership Period", "OP-1"): "ZZ reason"}})):
        mod, frappe, _ = _load(policy=policy, flags=flags, period_raises=no_period)
        doc = _doc(doctype="Ownership Period", name="OP-1", owner=owner, data_area_id="ZZ01")
        try:
            mod.check(doc, "before_submit")
        except ValueError as e:
            assert e is no_period
        else:
            raise AssertionError("period_of's refusal was swallowed")
        assert frappe.events == [] and doc.comments == []


def test_a_system_submit_records_one_approved_event_with_exempt_system():
    # R01g (#305-W2-S4): no self-approval judgment, no policy read, no
    # preparer lookup -- the actor is whoever frappe.session.user is during
    # the patch/install/migrate (Administrator, typically).
    for flag in ("in_patch", "in_install", "in_migrate"):
        mod, frappe, reads = _load(policy="Blocked", flags={flag: True})
        doc_a = _doc()
        doc_b = _doc(name="ICB-2", owner=OTHER)
        mod.check(doc_a, "before_submit")
        mod.check(doc_b, "before_submit")
        assert doc_a.comments == [] and doc_b.comments == []
        assert [e["kind"] for e in frappe.events] == ["approved", "approved"], frappe.events
        assert [e["reference_name"] for e in frappe.events] == ["ICB-1", "ICB-2"], frappe.events
        assert [e["reason"] for e in frappe.events] == [None, None], frappe.events
        assert frappe.events[0]["detail"] == {
            "preparer": ADMIN, "preparers": [ADMIN], "policy": None, "exempt": "system"
        }, frappe.events[0]
        assert frappe.events[1]["detail"] == {
            "preparer": OTHER, "preparers": [OTHER], "policy": None, "exempt": "system"
        }, frappe.events[1]
        assert reads == [], "a system submit reads nothing (no self-approval judgment)"


def test_a_system_submit_when_the_close_event_table_does_not_exist_writes_nothing():
    # Guard: a patch that runs before migrate's schema sync creates the Close
    # Event table. No crash, nothing written, nothing read.
    for flag in ("in_patch", "in_install", "in_migrate"):
        mod, frappe, reads = _load(policy="Blocked", flags={flag: True}, table_exists=False)
        mod.check(_doc(), "before_submit")
        assert frappe.events == [], frappe.events
        assert reads == [], reads


def test_a_derived_ownership_period_under_blocked_records_approved_with_exempt_derived():
    mod, frappe, _ = _load(policy="Blocked", flags={"from_business_combination": True})
    mod.check(_doc(doctype="Ownership Period", name="OP-1", data_area_id="ZZ01"),
              "before_submit")
    assert len(frappe.events) == 1, frappe.events
    event = frappe.events[0]
    assert event["kind"] == "approved", event
    assert event["reason"] is None and event["entity"] == "ZZ01", event
    assert event["detail"] == {"preparer": ADMIN, "preparers": [ADMIN], "policy": None,
                               "exempt": "derived"}, event


def test_a_doctype_outside_the_list_records_no_event():
    for dt in ("Trial Balance Submission", "TB Exception", "Budget Cycle", "ToDo"):
        mod, frappe, _ = _load(policy="Blocked")
        mod.check(_doc(doctype=dt), "before_submit")
        mod.check(_doc(doctype=dt, owner=OTHER), "before_submit")
        assert frappe.events == []


def test_the_event_carries_the_entity_from_entity_of():
    # #305-W2-9: an Ownership Period is scoped by its entity; a Group Exchange
    # Rate is group-level (entity None, visible to every trail reader).
    mod, frappe, _ = _load(policy="Blocked")
    mod.check(_doc(doctype="Ownership Period", name="OP-1", owner=OTHER,
                   data_area_id="ZZ01"), "before_submit")
    mod.check(_doc(doctype="Group Exchange Rate", name="GER-1", owner=OTHER),
              "before_submit")
    assert [(e["reference_doctype"], e["entity"]) for e in frappe.events] == [
        ("Ownership Period", "ZZ01"), ("Group Exchange Rate", None)], frappe.events


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
    # T02d (#305-W2-8) adds the cancel event beside the check; S42
    # (#305-W4-4 4c) adds the data-change hook's on_submit and (second,
    # after the cancel event) on_cancel.
    assert found == [{"before_submit": "konsol.close.self_approval.check",
                      "on_submit": "konsol.close.data_change_hook.on_submit",
                      "on_cancel": ["konsol.close.cancel_event.record",
                                    "konsol.close.data_change_hook.on_cancel"]}], found


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


"""konsol#305 T02d (#305-W2-8): cancelling an approval document records
``approval_cancelled``.

``konsol.close.cancel_event.record`` runs as ``doc_events["*"]["on_cancel"]``.
Frappe runs a doc_events hook after the controller's own method of the same
name (frappe/model/document.py ``Document.hook``: ``compose(fn, *hooks)``),
and ``on_cancel`` after ``before_cancel`` and the docstatus write
(document.py ``run_before_save_methods`` / ``run_post_save_methods``). Every
approval doctype refuses a cancel in ``before_cancel``, so a refused cancel
never reaches the hook; the event is written in the cancel's own transaction,
and a writer that raises stops the cancel.

The module is loaded by path against a stub frappe (copied from
test_close_self_approval.py, not imported). The pure policy and event models
and the T02a writer's ``entity_of`` are the product's own.
"""
import ast
import importlib.util
import os
import sys
import types

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CANCEL_PY = os.path.join(APP_DIR, "close", "cancel_event.py")
HOOK_PY = os.path.join(APP_DIR, "close", "self_approval.py")
MODEL_PY = os.path.join(APP_DIR, "close", "close_policy_model.py")
EVENT_MODEL_PY = os.path.join(APP_DIR, "close", "close_event_model.py")
WRITER_PY = os.path.join(APP_DIR, "close", "close_event.py")
HOOKS = os.path.join(APP_DIR, "hooks.py")

LEAD = "lead@example.com"
ANALYST = "analyst@example.com"

CONTROLLERS = {
    "Group Exchange Rate": "group_exchange_rate",
    "Ownership Period": "ownership_period",
    "Historical Equity Rate": "historical_equity_rate",
    "IC Balance": "ic_balance",
    "Consolidation Journal": "consolidation_journal",
    "Business Combination": "business_combination",
    "Business Disposal": "business_disposal",
}


class _Flags(dict):
    def __getattr__(self, n):
        return self.get(n)

    def __setattr__(self, n, v):
        self[n] = v


def _by_path(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _restore(saved):
    for n, old in saved.items():
        if old is not None:
            sys.modules[n] = old
        else:
            sys.modules.pop(n, None)


def _writer(frappe, events, record_raises=None, period_raises=None):
    """The real T02a writer loaded by path under stubs, so ``entity_of`` is
    the product's own. ``record`` appends instead of inserting; ``period_of``
    returns (2025, 7) or raises ``period_raises``."""
    period_status = types.ModuleType("konsol.period_status")
    period_model = types.ModuleType("konsol.close.period_model")
    controller = types.ModuleType("konsol.consolidation.doctype.close_event")
    controller.close_event = types.SimpleNamespace()
    close_pkg = types.ModuleType("konsol.close")
    close_pkg.close_event_model = _by_path("close_event_model_for_t02d", EVENT_MODEL_PY)
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
        writer = _by_path("close_event_writer_for_t02d", WRITER_PY)
    finally:
        _restore(saved)

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


def _load(flags=None, record_raises=None, period_raises=None, table_exists=True):
    """``cancel_event`` loaded by path; its events are in ``frappe.events``."""
    frappe = types.ModuleType("frappe")
    frappe.ValidationError = type("ValidationError", (Exception,), {})
    frappe.PermissionError = type("PermissionError", (Exception,), {})

    def throw(msg, exc=None, **k):
        raise (exc or frappe.ValidationError)(msg)

    def table_exists_fn(doctype):
        assert doctype == "Close Event", doctype
        return table_exists

    frappe.throw = throw
    frappe.flags = _Flags(flags or {})
    frappe.session = types.SimpleNamespace(user=LEAD)
    frappe.db = types.SimpleNamespace(table_exists=table_exists_fn)
    frappe.logger = lambda: types.SimpleNamespace(
        warning=lambda *a, **k: None, info=lambda *a, **k: None)
    frappe.events = []
    writer = _writer(frappe, frappe.events, record_raises, period_raises)

    close_pkg = types.ModuleType("konsol.close")
    close_pkg.__path__ = [os.path.join(APP_DIR, "close")]
    model = _by_path("close_policy_model_for_t02d", MODEL_PY)
    close_pkg.close_policy_model = model
    close_pkg.close_event = writer
    close_pkg.close_event_model = _by_path("close_event_model_for_t02d", EVENT_MODEL_PY)
    konsol_pkg = types.ModuleType("konsol")
    konsol_pkg.__path__ = [APP_DIR]
    konsol_pkg.close = close_pkg
    mods = {"frappe": frappe, "konsol": konsol_pkg, "konsol.close": close_pkg,
            "konsol.close.close_policy_model": model,
            "konsol.close.close_event": writer,
            "konsol.close.close_event_model": close_pkg.close_event_model}
    saved = {n: sys.modules.get(n) for n in mods}
    sys.modules.update(mods)
    try:
        close_pkg.self_approval = _by_path("self_approval_for_t02d", HOOK_PY)
        mods["konsol.close.self_approval"] = close_pkg.self_approval
        sys.modules["konsol.close.self_approval"] = close_pkg.self_approval
        saved.setdefault("konsol.close.self_approval", None)
        module = _by_path("cancel_event_under_test", CANCEL_PY)
    finally:
        _restore(saved)

    real_record = module.record

    def record(doc, method=None):
        saved_now = {n: sys.modules.get(n) for n in mods}
        sys.modules.update(mods)
        try:
            return real_record(doc, method)
        finally:
            _restore(saved_now)

    module.record = record
    return module, frappe


def _doc(doctype="Group Exchange Rate", name="GER-1", owner=ANALYST, **fields):
    # A Frappe document has every field of its doctype; blank unless given.
    fields = dict({"data_area_id": None, "acquired_entity": None, "disposed_entity": None},
                  **fields)
    return types.SimpleNamespace(doctype=doctype, name=name, owner=owner, **fields)


def _raises(fn, exc):
    try:
        fn()
    except exc as e:
        return e
    raise AssertionError("expected %s" % exc.__name__)


class _Txn:
    """A stand-in for the request's MariaDB transaction: the docstatus write
    and every event are pending until the cancel returns; an exception rolls
    both back, as Frappe's request handler does."""

    def __init__(self, frappe):
        self.frappe = frappe
        self.committed = []
        self.docstatus = {}

    def cancel(self, doc, hook, before_cancel=None):
        """Frappe's order (document.py): before_cancel; the docstatus write;
        on_cancel = the controller's method, then doc_events[doctype], then
        doc_events["*"] (Document.hook's compose)."""
        mark = len(self.frappe.events)
        pending = {}
        try:
            if before_cancel:
                before_cancel(doc)
            pending[doc.name] = 2
            hook(doc, "on_cancel")
        except Exception:
            del self.frappe.events[mark:]
            raise
        self.docstatus.update(pending)
        self.committed.extend(self.frappe.events[mark:])


# --- the hook ----------------------------------------------------------------

def test_a_group_rate_cancel_records_one_group_level_event():
    mod, frappe = _load()
    mod.record(_doc(), "on_cancel")
    assert len(frappe.events) == 1, frappe.events
    e = frappe.events[0]
    assert e["kind"] == "approval_cancelled"
    assert (e["fiscal_year"], e["fiscal_period"]) == (2025, 7)
    assert (e["reference_doctype"], e["reference_name"]) == ("Group Exchange Rate", "GER-1")
    assert e["reason"] is None, "Frappe's cancel takes no reason"
    assert e["entity"] is None, "a group rate is group-level (W2-9)"
    assert e["detail"] == {"preparer": ANALYST, "exempt": None}, e["detail"]


def test_an_ownership_period_cancel_carries_its_entity():
    mod, frappe = _load()
    mod.record(_doc("Ownership Period", "OP-1", data_area_id="ZZ01"), "on_cancel")
    assert [e["entity"] for e in frappe.events] == ["ZZ01"]


def test_every_approval_doctype_records_and_the_kind_is_declared():
    model = _by_path("close_event_model_for_t02d_kinds", EVENT_MODEL_PY)
    assert "approval_cancelled" in model.KINDS
    for dt in _by_path("close_policy_model_for_t02d_list", MODEL_PY).APPROVAL_DOCTYPES:
        mod, frappe = _load()
        mod.record(_doc(dt, "X-1"), "on_cancel")
        assert [(e["kind"], e["reference_doctype"]) for e in frappe.events] == [
            ("approval_cancelled", dt)], (dt, frappe.events)


def test_a_tb_cancel_is_not_an_approval_and_records_nothing():
    # T05 records tb_cancelled itself.
    for dt in ("Trial Balance Submission", "TB Exception", "Budget Cycle", "ToDo"):
        mod, frappe = _load()
        mod.record(_doc(dt, "X-1"), "on_cancel")
        assert frappe.events == [], dt


def test_a_derived_ownership_period_cancel_is_recorded_as_derived():
    # A Business Combination cancel cancels its own Ownership Period under the
    # flag: two events, the BC's and the OP's.
    mod, frappe = _load(flags={"from_business_combination": True})
    mod.record(_doc("Ownership Period", "OP-9", data_area_id="ZZ02"), "on_cancel")
    assert [e["detail"]["exempt"] for e in frappe.events] == ["derived"]


def test_a_system_cancel_records_one_approval_cancelled_event_with_exempt_system():
    # R01g (#305-W2-S4): a patch, install or migrate cancel records live too.
    for flag in ("in_patch", "in_install", "in_migrate"):
        mod, frappe = _load(flags={flag: True})
        mod.record(_doc(), "on_cancel")
        assert len(frappe.events) == 1, flag
        event = frappe.events[0]
        assert event["kind"] == "approval_cancelled", event
        assert event["reason"] is None, "Frappe's cancel takes no reason"
        assert event["detail"] == {"preparer": ANALYST, "exempt": "system"}, event


def test_a_system_cancel_when_the_close_event_table_does_not_exist_writes_nothing():
    # Guard: a patch that runs before migrate's schema sync creates the Close
    # Event table. No crash, nothing written.
    for flag in ("in_patch", "in_install", "in_migrate"):
        mod, frappe = _load(flags={flag: True}, table_exists=False)
        mod.record(_doc(), "on_cancel")
        assert frappe.events == [], flag


def test_a_raising_writer_propagates_uncaught():
    boom = RuntimeError("insert failed")
    mod, frappe = _load(record_raises=boom)
    assert _raises(lambda: mod.record(_doc(), "on_cancel"), RuntimeError) is boom


def test_no_declared_period_propagates_and_records_nothing():
    # W2-5: an Ownership Period with no declared period refuses the cancel.
    mod, frappe = _load(period_raises=frappe_validation())
    e = _raises(lambda: mod.record(_doc("Ownership Period", "OP-1", data_area_id="ZZ01"),
                                   "on_cancel"), Exception)
    assert "declare the period" in str(e)
    assert frappe.events == []


def frappe_validation():
    return ValueError("Ownership Period OP-1 changes no declared fiscal period: "
                      "declare the period in EPM Fiscal Year first.")


# --- the cancel's transaction ------------------------------------------------

def test_a_refused_cancel_writes_nothing():
    # The refusal is in before_cancel, which runs before the hook.
    mod, frappe = _load()
    txn = _Txn(frappe)

    def refuse(doc):
        frappe.throw("Period 2025-07 is Closed: reopen it first.")

    _raises(lambda: txn.cancel(_doc(), mod.record, before_cancel=refuse),
            frappe.ValidationError)
    assert frappe.events == [] and txn.committed == [] and txn.docstatus == {}


def test_a_failing_writer_stops_the_cancel():
    mod, frappe = _load(record_raises=RuntimeError("insert failed"))
    txn = _Txn(frappe)
    _raises(lambda: txn.cancel(_doc(), mod.record), RuntimeError)
    assert txn.docstatus == {}, "the cancel must not complete without its event"
    assert txn.committed == []


def test_a_completed_cancel_commits_its_event_with_it():
    mod, frappe = _load()
    txn = _Txn(frappe)
    txn.cancel(_doc(), mod.record)
    assert txn.docstatus == {"GER-1": 2}
    assert [e["kind"] for e in txn.committed] == ["approval_cancelled"]


def test_every_approval_doctype_refuses_in_before_cancel_and_syncs_in_on_cancel():
    # Pins the order the hook relies on: each controller's refusal is in
    # before_cancel (before the hook) and its on_cancel exists (the hook runs
    # after it, inside the same transaction).
    model = _by_path("close_policy_model_for_t02d_ctl", MODEL_PY)
    assert set(CONTROLLERS) == set(model.APPROVAL_DOCTYPES)
    for dt, module in CONTROLLERS.items():
        path = os.path.join(APP_DIR, "consolidation", "doctype", module, module + ".py")
        with open(path) as f:
            tree = ast.parse(f.read())
        methods = {n.name for c in tree.body if isinstance(c, ast.ClassDef)
                   for n in c.body if isinstance(n, ast.FunctionDef)}
        assert {"before_cancel", "on_cancel"} <= methods, (dt, sorted(methods))


def test_the_hook_neither_commits_nor_catches():
    with open(CANCEL_PY) as f:
        src = f.read()
    tree = ast.parse(src)
    assert not [n for n in ast.walk(tree) if isinstance(n, ast.Try)], "no try/except"
    assert "commit(" not in src and "rollback(" not in src


# --- wiring ------------------------------------------------------------------

def test_hooks_wire_the_cancel_event_as_the_star_on_cancel():
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
    # S42 (#305-W4-4 4c): the data-change hook joins the star on_submit and
    # (second, after this event) the star on_cancel.
    assert found == [{"before_submit": "konsol.close.self_approval.check",
                      "on_submit": "konsol.close.data_change_hook.on_submit",
                      "on_cancel": ["konsol.close.cancel_event.record",
                                    "konsol.close.data_change_hook.on_cancel"]}], found

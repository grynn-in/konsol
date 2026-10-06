"""konsol#305 story 9.4 (#157), #305-W5-1 (Deepak Pai, 6 Oct 2026): the
Close Lead rejects a signature with a typed reason.

- ``reject_signoff(close_run, reason)`` puts a signed, acknowledged or
  overridden run back to "Not Signed Off" and clears the signature fields,
  through the sign-off writer, in the same transaction as one
  ``signoff_rejected`` Close Event. The event keeps what was rejected (the
  signature's state, who signed and when) and names the preparer: the user
  who triggered the run, who gets the "sent back" My work item.
- Only the Close Lead (EPM Admin, System Manager) may reject; a blank reason,
  an unsigned or Re-sign Needed run, and a period that is not Open are
  refused before anything is written.
- It is not whitelisted: the only door is ``signoff_api.reject``, which adds
  the stale-run check (A58).
- Rejected (W5-1): a new "Rejected" run state; a comment only.

Runs ``assertion_run.py`` against test_assertion_warn_amber.py's stub frappe
(its ``_load``, loaded by path). The Close Event writer is stubbed, but each
event it receives is checked with the REAL ``close_event_model.event_problems``
(the rule ``close_event.record`` applies), so the producer's output is one the
real writer accepts.
"""
import ast
import importlib.util
import os
import sys
import types

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
AR_PY = os.path.join(APP_DIR, "consolidation", "doctype", "assertion_run", "assertion_run.py")

_spec = importlib.util.spec_from_file_location(
    "warn_amber_for_reject", os.path.join(TESTS_DIR, "test_assertion_warn_amber.py"))
_amber = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_amber)

_cem_spec = importlib.util.spec_from_file_location(
    "close_event_model_for_reject", os.path.join(APP_DIR, "close", "close_event_model.py"))
close_event_model = importlib.util.module_from_spec(_cem_spec)
_cem_spec.loader.exec_module(close_event_model)

LEAD_ROLES = ("EPM Admin",)
PREPARER = "ana@example.com"
SIGNER = "dana@example.com"
SIGNED_AT = "2026-10-05 09:30:00"


def load(signoff_status="Signed Off", roles=LEAD_ROLES, period_status="Open",
         record_raises=False):
    """``(reject, frappe, doc)``: ``reject(close_run, reason)`` calls the real
    ``reject_signoff`` with a recording Close Event writer installed. The run
    is AR-1, FY2099 P01, Green, signed by SIGNER, triggered by PREPARER."""
    module, frappe, doc, _ = _amber._load(status="Green", warned=0, roles=roles)
    doc.signoff_status = signoff_status
    doc.triggered_by = PREPARER
    if signoff_status in ("Signed Off", "Acknowledged", "Overridden"):
        doc.signed_off_by = SIGNER
        doc.signed_off_at = SIGNED_AT
        doc.acknowledgement = "seen" if signoff_status == "Acknowledged" else None
        doc.override_reason = "known" if signoff_status == "Overridden" else None
        doc.warnings_at_signoff = "assert_a" if signoff_status == "Acknowledged" else None
    module.period_row = lambda fy, fp: {"code": "P%02d" % int(fp), "status": period_status}

    close_event = types.ModuleType("konsol.close.close_event")

    def record(kind, fiscal_year, fiscal_period, reference_doctype=None,
               reference_name=None, reason=None, detail=None, entity=None):
        if record_raises:
            raise RuntimeError("the Close Event writer failed")
        # What close_event.record builds and checks (close_event.py:record).
        event = {"kind": kind, "fiscal_year": fiscal_year, "fiscal_period": fiscal_period,
                 "entity": entity, "reference_doctype": reference_doctype,
                 "reference_name": reference_name, "actor": frappe.session.user,
                 "at": "NOW", "reason": (reason or "").strip() or None,
                 "detail": dict(detail or {}, actor_persona="close_lead"), "source": "live"}
        problems = close_event_model.event_problems(event)
        assert problems == [], problems
        frappe.events.append(dict(event, saved_before=doc.signoff_saved))
        return "CE-000000001"

    close_event.record = record
    close_pkg = types.ModuleType("konsol.close")
    close_pkg.close_event = close_event

    def reject(*a, **k):
        names = ("konsol.close", "konsol.close.close_event")
        before = {n: sys.modules.get(n) for n in names}
        sys.modules.update({"konsol.close": close_pkg, "konsol.close.close_event": close_event})
        try:
            return module.reject_signoff(*a, **k)
        finally:
            for n, old in before.items():
                if old is None:
                    sys.modules.pop(n, None)
                else:
                    sys.modules[n] = old

    return reject, frappe, doc


def _refused(reject, frappe, *args, exc=None):
    exc = exc or frappe.ValidationError
    try:
        reject(*args)
    except exc as e:
        return str(e)
    raise AssertionError("reject_signoff did not refuse %r" % (args,))


def _nothing_written(frappe, doc, status):
    assert doc.signoff_saved is False
    assert doc.signoff_status == status
    assert frappe.events == []


# --- the reject -----------------------------------------------------------------

def test_the_close_lead_rejects_a_signed_run_back_to_not_signed():
    reject, frappe, doc = load()
    result = reject("AR-1", "  ZZA's TB is the draft  ")
    assert doc.signoff_saved is True
    assert doc.signoff_status == "Not Signed Off"
    for field in ("signed_off_by", "signed_off_at", "override_reason", "acknowledgement",
                  "warnings_at_signoff"):
        assert getattr(doc, field) is None, field
    assert result == {"signoff_status": "Not Signed Off"}


def test_the_reject_writes_one_signoff_rejected_event_after_the_save_and_before_the_commit():
    reject, frappe, doc = load()
    reject("AR-1", "ZZA's TB is the draft")
    event, commit = frappe.events
    assert commit == "commit"
    assert event["saved_before"] is True
    assert event["kind"] == "signoff_rejected"
    assert (event["fiscal_year"], event["fiscal_period"]) == (2099, 1)
    assert (event["reference_doctype"], event["reference_name"]) == ("Assertion Run", "AR-1")
    assert event["reason"] == "ZZA's TB is the draft"
    assert event["entity"] is None


def test_the_event_keeps_the_rejected_signature_and_names_the_preparer():
    reject, frappe, doc = load("Acknowledged")
    reject("AR-1", "Commentary missing on NET SALES")
    detail = frappe.events[0]["detail"]
    assert detail["preparer"] == PREPARER
    assert detail["signoff_status"] == "Acknowledged"
    assert detail["signed_off_by"] == SIGNER
    assert detail["signed_off_at"] == SIGNED_AT
    assert detail["acknowledgement"] == "seen"
    assert detail["run_status"] == "Green"


def test_each_signed_state_can_be_rejected():
    for state in ("Signed Off", "Acknowledged", "Overridden"):
        reject, frappe, doc = load(state)
        reject("AR-1", "rework")
        assert doc.signoff_status == "Not Signed Off", state
        assert frappe.events[0]["detail"]["signoff_status"] == state


# --- refusals: nothing is written ------------------------------------------------

def test_a_blank_reason_is_refused_and_nothing_is_written():
    for reason in (None, "", "   \n"):
        reject, frappe, doc = load()
        message = _refused(reject, frappe, "AR-1", reason)
        assert "reason" in message, message
        _nothing_written(frappe, doc, "Signed Off")


def test_only_the_close_lead_may_reject():
    for roles in (("EPM Analyst",), ("Entity Accountant",), ("EPM User",), ()):
        reject, frappe, doc = load(roles=roles)
        message = _refused(reject, frappe, "AR-1", "rework", exc=frappe.PermissionError)
        assert "Close Lead" in message, message
        _nothing_written(frappe, doc, "Signed Off")


def test_system_manager_is_a_close_lead_too():
    reject, frappe, doc = load(roles=("System Manager",))
    reject("AR-1", "rework")
    assert doc.signoff_status == "Not Signed Off"


def test_an_unsigned_run_cannot_be_rejected():
    for state in ("Not Signed Off", "Re-sign Needed"):
        reject, frappe, doc = load(state)
        message = _refused(reject, frappe, "AR-1", "rework")
        assert state in message and "Nothing was changed" in message, message
        _nothing_written(frappe, doc, state)


def test_a_closed_or_locked_period_is_refused():
    for status in ("Closed", "Locked"):
        reject, frappe, doc = load(period_status=status)
        message = _refused(reject, frappe, "AR-1", "rework")
        assert status in message and "reopen" in message, message
        _nothing_written(frappe, doc, "Signed Off")


def test_a_writer_failure_propagates_and_nothing_is_committed():
    reject, frappe, doc = load(record_raises=True)
    try:
        reject("AR-1", "rework")
    except RuntimeError:
        pass
    else:
        raise AssertionError("the writer failure was swallowed")
    assert "commit" not in frappe.events


# --- the door -------------------------------------------------------------------

def test_reject_signoff_is_not_whitelisted():
    """Only signoff_api.reject reaches it, with the stale-run check (A58)."""
    with open(AR_PY) as f:
        tree = ast.parse(f.read())
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "reject_signoff")
    assert fn.decorator_list == []

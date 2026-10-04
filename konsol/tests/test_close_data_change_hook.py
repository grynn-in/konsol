"""konsol#305 S42: every submit and cancel of a NUMBER_DRIVING approval
doctype calls ``signoff_gate.record_data_change`` for each period
``data_change_model.changed_periods`` names.

``konsol.close.data_change_hook`` is wired in ``hooks.py`` as
``doc_events["*"]["on_submit"]`` and (after ``cancel_event.record``) as a
second ``doc_events["*"]["on_cancel"]`` entry (Frappe runs a list of
handlers for one event, in order — ``append_hook``, frappe/__init__.py).

Loaded by path against a stub frappe/konsol (pattern copied from
test_close_cancel_event.py's ``_load``, not imported). ``data_change_model``
is the REAL module (pure, no frappe/konsol import, S41) loaded the same way
test_close_data_change_model.py loads it, so the hook's own
``from konsol.close import data_change_model`` resolves to it.
``signoff_gate.record_data_change`` and ``close_event.period_of`` /
``close_event.entity_of`` are stubs this file controls; through the REAL
signoff_gate is out of scope here (test_close_signoff_gate.py covers it), so
each test asserts the stub was called with exactly what
``record_data_change(fiscal_year, fiscal_period, text, user, entity=None)``
takes.
"""
import ast
import importlib.util
import os
import sys
import types

import pytest

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOOK_PY = os.path.join(APP_DIR, "close", "data_change_hook.py")
MODEL_PY = os.path.join(APP_DIR, "close", "data_change_model.py")
HOOKS = os.path.join(APP_DIR, "hooks.py")

USER = "zz-acct@example.com"


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


class _Doc(dict):
    """A Frappe document has every field; ``.get`` reads one back (blank
    unless given), ``.doctype`` / ``.name`` are plain attributes."""

    def __getattr__(self, name):
        return self.get(name)


def _doc(doctype, name, **fields):
    return _Doc(doctype=doctype, name=name, **fields)


def _row(fiscal_year, fiscal_period):
    """A minimal fiscal_calendar.fiscal_period_rows() row: changed_periods
    reads only these two keys."""
    return {"fiscal_year": fiscal_year, "fiscal_period": fiscal_period}


def _load(period_rows=(), period_of=None, entity_of=None, table_exists=True,
          record_raises=None, user=USER):
    """``data_change_hook`` loaded by path; its calls land in
    ``frappe.calls`` (``[(fy, fp, text, user, entity), ...]``)."""
    frappe = types.ModuleType("frappe")
    frappe.calls = []
    frappe.session = types.SimpleNamespace(user=user)
    frappe.db = types.SimpleNamespace(table_exists=lambda dt: table_exists)
    frappe.logger = lambda: types.SimpleNamespace(
        warning=lambda *a, **k: None, info=lambda *a, **k: None)

    def record_data_change(fiscal_year, fiscal_period, text, user, entity=None):
        if record_raises:
            raise record_raises
        frappe.calls.append((fiscal_year, fiscal_period, text, user, entity))
        return []

    signoff_gate = types.SimpleNamespace(record_data_change=record_data_change)

    def _period_of(doc):
        return period_of(doc) if period_of else (2025, 7)

    def _entity_of(doc):
        return entity_of(doc) if entity_of else None

    close_event = types.SimpleNamespace(period_of=_period_of, entity_of=_entity_of)

    fiscal_calendar = types.ModuleType("konsol.fiscal_calendar")
    fiscal_calendar.fiscal_period_rows = lambda: list(period_rows)

    data_change_model = _by_path("data_change_model_for_s42", MODEL_PY)

    close_pkg = types.ModuleType("konsol.close")
    close_pkg.__path__ = [os.path.join(APP_DIR, "close")]
    close_pkg.data_change_model = data_change_model
    close_pkg.close_event = close_event
    close_pkg.signoff_gate = signoff_gate
    konsol_pkg = types.ModuleType("konsol")
    konsol_pkg.__path__ = [APP_DIR]
    konsol_pkg.close = close_pkg
    konsol_pkg.fiscal_calendar = fiscal_calendar

    mods = {
        "frappe": frappe,
        "konsol": konsol_pkg,
        "konsol.close": close_pkg,
        "konsol.close.data_change_model": data_change_model,
        "konsol.close.close_event": close_event,
        "konsol.close.signoff_gate": signoff_gate,
        "konsol.fiscal_calendar": fiscal_calendar,
    }
    saved = {n: sys.modules.get(n) for n in mods}
    sys.modules.update(mods)
    try:
        module = _by_path("data_change_hook_under_test", HOOK_PY)
    finally:
        _restore(saved)
    return module, frappe


# --- on_submit: a doctype outside NUMBER_DRIVING records nothing -----------

def test_a_trial_balance_submission_and_a_build_approval_record_nothing():
    module, frappe = _load()
    for dt in ("Trial Balance Submission", "Build Approval"):
        module.on_submit(_doc(dt, "X-1"))
        module.on_cancel(_doc(dt, "X-1"))
    assert frappe.calls == []


# --- on_submit: NUMBER_DRIVING doctypes ------------------------------------

def test_a_ger_on_submit_records_one_call():
    module, frappe = _load(period_rows=[_row(2025, 7)], period_of=lambda d: (2025, 7))
    module.on_submit(_doc("Group Exchange Rate", "GER-1"))
    assert frappe.calls == [
        (2025, 7, "Group Exchange Rate GER-1 approved", USER, None)], frappe.calls


def test_an_ownership_period_on_submit_names_its_entity():
    module, frappe = _load(
        period_rows=[_row(2025, 12)], period_of=lambda d: (2025, 12),
        entity_of=lambda d: "ZZ01")
    module.on_submit(_doc("Ownership Period", "OP-1"))
    assert frappe.calls == [
        (2025, 12, "Ownership Period OP-1 approved", USER, "ZZ01")], frappe.calls


def test_a_journal_reversing_into_a_later_period_records_two_calls():
    module, frappe = _load(
        period_rows=[_row(2025, 7), _row(2025, 8)], period_of=lambda d: (2025, 7))
    doc = _doc("Consolidation Journal", "CJ-1",
                reverse_fiscal_year=2025, reverse_fiscal_period=8)
    module.on_submit(doc)
    assert frappe.calls == [
        (2025, 7, "Consolidation Journal CJ-1 approved", USER, None),
        (2025, 8, "Consolidation Journal CJ-1 approved", USER, None)], frappe.calls


def test_an_ic_balance_is_number_driving_and_records_a_call():
    """AMENDED 4 Oct (Deepak "all ★", #305 issuecomment-5978983396): IC
    Balance is in NUMBER_DRIVING (S41, merged) — not excluded."""
    module, frappe = _load(period_rows=[_row(2025, 9)], period_of=lambda d: (2025, 9))
    module.on_submit(_doc("IC Balance", "ICB-1"))
    assert frappe.calls == [
        (2025, 9, "IC Balance ICB-1 approved", USER, None)], frappe.calls


# --- on_cancel --------------------------------------------------------------

def test_on_cancel_of_a_journal_records_the_cancelled_text():
    """A Consolidation Journal's Reverse IS this doctype's on_cancel
    (consolidation_journal.py: "submit is the approval, cancel the
    reversal"); no separate wiring and no new change_text action are
    needed — the hook records it exactly as any other cancel."""
    module, frappe = _load(period_rows=[_row(2025, 7)], period_of=lambda d: (2025, 7))
    module.on_cancel(_doc("Consolidation Journal", "CJ-1"))
    assert frappe.calls == [
        (2025, 7, "Consolidation Journal CJ-1 cancelled", USER, None)], frappe.calls


def test_on_cancel_of_a_ger_records_the_cancelled_text():
    module, frappe = _load(period_rows=[_row(2025, 7)], period_of=lambda d: (2025, 7))
    module.on_cancel(_doc("Group Exchange Rate", "GER-1"))
    assert frappe.calls == [
        (2025, 7, "Group Exchange Rate GER-1 cancelled", USER, None)], frappe.calls


# --- failure paths -----------------------------------------------------------

def test_a_record_data_change_failure_propagates():
    module, frappe = _load(
        period_rows=[_row(2025, 7)], period_of=lambda d: (2025, 7),
        record_raises=RuntimeError("writer down"))
    with pytest.raises(RuntimeError, match="writer down"):
        module.on_submit(_doc("Group Exchange Rate", "GER-1"))


def test_close_event_table_missing_records_nothing():
    """R01g precedent (self_approval.py / cancel_event.py): while the Close
    Event table does not exist yet (a patch before migrate's schema sync),
    nothing is recorded — record_data_change's mark writes a Close Event."""
    module, frappe = _load(period_rows=[_row(2025, 7)], table_exists=False)
    module.on_submit(_doc("Group Exchange Rate", "GER-1"))
    module.on_cancel(_doc("Group Exchange Rate", "GER-1"))
    assert frappe.calls == []


# --- module contract ----------------------------------------------------

def test_the_hook_neither_commits_nor_catches():
    with open(HOOK_PY) as f:
        src = f.read()
    tree = ast.parse(src)
    assert not [n for n in ast.walk(tree) if isinstance(n, ast.Try)], "no try/except"
    assert "commit(" not in src and "rollback(" not in src


# --- hooks.py wiring -------------------------------------------------------

def test_hooks_wire_the_data_change_hook():
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
    assert found == [{
        "before_submit": "konsol.close.self_approval.check",
        "on_submit": "konsol.close.data_change_hook.on_submit",
        "on_cancel": ["konsol.close.cancel_event.record",
                      "konsol.close.data_change_hook.on_cancel"],
    }], found

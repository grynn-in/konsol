"""Budget Cycle's installed workflow (konsol#348 S2-C W1, BUD-9-3).

Submit is the approval: a Cycle is reviewed while it is a draft, and the
Approve transition is the submit. EPM Analyst sends it for approval; EPM Admin
approves or rejects. The state lives in the Cycle's own ``workflow_state``
field, and the workflow is installed once, so a site may change it.
"""
import json
import os
import re

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATH = os.path.join(APP_DIR, "epm", "doctype", "budget_cycle", "budget_cycle_workflow.json")


def _workflow():
    assert os.path.exists(PATH), "no budget_cycle_workflow.json"
    with open(PATH) as f:
        return json.load(f)


def _installed():
    with open(os.path.join(APP_DIR, "workflows.py")) as f:
        return re.findall(r'"([^"]+)"', f.read().split("INSTALLED = (")[1].split(")")[0])


def test_budget_cycle_workflow_is_installed():
    assert "Budget Cycle" in _installed()


def test_the_workflow_names_its_doctype_and_state_field():
    wf = _workflow()
    assert wf["doctype"] == "Workflow"
    assert wf["document_type"] == "Budget Cycle"
    assert wf["name"] == wf["workflow_name"] == "Budget Cycle Workflow"
    assert wf["workflow_state_field"] == "workflow_state"
    assert wf["is_active"] == 1


def test_states_and_their_docstatus():
    states = {s["state"]: (str(s["doc_status"]), s["allow_edit"]) for s in _workflow()["states"]}
    assert states == {
        "Draft": ("0", "EPM Analyst"),
        "Pending Approval": ("0", "EPM Admin"),
        "Approved": ("1", "EPM Admin"),
        "Cancelled": ("2", "EPM Admin"),
    }
    # The first state is the one a new Cycle starts in (Q17-b reads it).
    assert _workflow()["states"][0]["state"] == "Draft"


def test_transitions_per_the_part_2_table():
    got = [(t["state"], t["action"], t["next_state"], t["allowed"]) for t in _workflow()["transitions"]]
    assert got == [
        ("Draft", "Send for Approval", "Pending Approval", "EPM Analyst"),
        ("Pending Approval", "Reject", "Draft", "EPM Admin"),
        ("Pending Approval", "Approve", "Approved", "EPM Admin"),
    ]


def test_no_transition_leads_into_cancelled():
    """Cancel runs through the Cycle's before_cancel (the closed-year gate),
    as a plain Cancel: no workflow action leads to the docstatus-2 state."""
    assert all(t["next_state"] != "Cancelled" for t in _workflow()["transitions"])


# --- cancel while an Approved Budget names the Cycle (konsol#348 S5 K16; S5-Q5 a) ---
#
# Budget Cycle.before_cancel refuses, by name, while a SUBMITTED Approved
# Budget names the Cycle: the Consolidated Statement's Budget columns read
# that declaration, and cancelling the Cycle under it would turn the budget
# into a read-time state silently. A draft or cancelled declaration does not
# count; once the declaration is cancelled the Cycle cancels as before. The
# period gate (assert_year_not_closed) still runs first, and nothing is
# assigned before either gate. The controller is loaded by path against a
# stub frappe whose get_all serves the Approved Budget rows.

import contextlib
import importlib.util
import pytest
import sys
import types

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CYCLE_PY = os.path.join(APP_DIR, "epm", "doctype", "budget_cycle", "budget_cycle.py")
CYCLE_NAME = "BCYC-ZZB-2027-abc12345"


class _Thrown(Exception):
    pass


@contextlib.contextmanager
def _cycle_env(approved_budgets, closed_years=()):
    """The Budget Cycle controller against a stub frappe, installed for the
    whole block (before_cancel imports konsol.period_status at call time).
    Yields ``(doc, seen)``: ``seen`` records the get_all filters, the period
    gate calls and set_cancelled_state calls."""
    seen = {"get_all": [], "gate": [], "cancelled": []}

    class Document:
        def __init__(self, **kw):
            self.__dict__.update(kw)

        def get(self, key, default=None):
            return self.__dict__.get(key, default)

    def throw(msg, exc=None, *a, **k):
        raise _Thrown(msg)

    def get_all(doctype, filters=None, fields=None, order_by=None, limit_page_length=None, **k):
        seen["get_all"].append((doctype, dict(filters or {}), list(fields or []), order_by))
        assert doctype == "Approved Budget"
        rows = [r for r in approved_budgets
                if r["budget_cycle"] == filters["budget_cycle"] and r["docstatus"] == filters["docstatus"]]
        return [types.SimpleNamespace(**{f: r.get(f) for f in fields}) for r in rows]

    frappe = types.ModuleType("frappe")
    frappe._ = lambda s: s
    frappe.throw = throw
    frappe.ValidationError = _Thrown
    frappe.get_all = get_all
    frappe.db = types.SimpleNamespace()
    docmod = types.ModuleType("frappe.model.document")
    docmod.Document = Document

    def assert_year_not_closed(fiscal_year, action):
        seen["gate"].append((fiscal_year, action))
        if fiscal_year in closed_years:
            raise _Thrown(f"Cannot {action}: every period of FY{fiscal_year} is closed.")

    period_status = types.ModuleType("konsol.period_status")
    period_status.assert_year_not_closed = assert_year_not_closed
    workflows = types.ModuleType("konsol.workflows")
    workflows.set_cancelled_state = lambda doc: seen["cancelled"].append(doc)
    konsol = types.ModuleType("konsol")
    konsol.__path__ = []
    konsol.budget_bulk_model = types.ModuleType("konsol.budget_bulk_model")
    grain = types.ModuleType("konsol.epm.budget_grain")
    grain.digest_name = lambda prefix, parts: "X"
    mods = {"frappe": frappe, "frappe.model": types.ModuleType("frappe.model"),
            "frappe.model.document": docmod, "konsol": konsol,
            "konsol.budget_bulk_model": konsol.budget_bulk_model, "konsol.epm": types.ModuleType("konsol.epm"),
            "konsol.epm.budget_grain": grain, "konsol.workflows": workflows,
            "konsol.period_status": period_status}
    saved = {n: sys.modules.get(n) for n in mods}
    sys.modules.update(mods)
    try:
        spec = importlib.util.spec_from_file_location("budget_cycle_cancel_under_test", CYCLE_PY)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        yield module.BudgetCycle(name=CYCLE_NAME, fiscal_year=2027, scenario_id="ZZB", docstatus=1), seen
    finally:
        for n, old in saved.items():
            if old is None:
                sys.modules.pop(n, None)
            else:
                sys.modules[n] = old


def _declaration(name="APB-CG-ZZGRP--2027-0f0f0f0f", docstatus=1, cycle=CYCLE_NAME):
    return {"name": name, "docstatus": docstatus, "budget_cycle": cycle,
            "consolidation_group": "CG-ZZGRP-", "fiscal_year": 2027}


def test_cancel_is_refused_by_name_while_a_submitted_approved_budget_names_the_cycle():
    with _cycle_env([_declaration()]) as (doc, seen):
        with pytest.raises(_Thrown) as info:
            doc.before_cancel()
    message = str(info.value)
    assert CYCLE_NAME in message and "APB-CG-ZZGRP--2027-0f0f0f0f" in message, message
    assert "Approved Budget" in message and "cancel" in message.lower()
    assert seen["cancelled"] == [], "nothing changes before the gate"
    (doctype, filters, fields, order_by), = seen["get_all"]
    assert filters == {"budget_cycle": CYCLE_NAME, "docstatus": 1}, "submitted declarations only"
    assert order_by, "a deterministic order (v16 sorts by creation otherwise)"


def test_cancel_is_allowed_once_the_declaration_is_cancelled_or_was_never_submitted():
    for declarations in ([], [_declaration(docstatus=2)], [_declaration(docstatus=0)],
                         [_declaration(cycle="BCYC-ZZB-2027-other")]):
        with _cycle_env(declarations) as (doc, seen):
            doc.before_cancel()
        assert seen["cancelled"] == [doc], declarations
        assert seen["gate"] == [(2027, "cancel Budget Cycle %s" % CYCLE_NAME)]


def test_the_period_gate_still_runs_first():
    with _cycle_env([_declaration()], closed_years={2027}) as (doc, seen):
        with pytest.raises(_Thrown, match="FY2027"):
            doc.before_cancel()
    assert seen["get_all"] == [], "the closed year refuses before the Approved Budget read"
    assert seen["cancelled"] == []

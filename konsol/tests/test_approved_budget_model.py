"""Approved budget state model, pure: konsol/close/approved_budget_model.py
(konsol#348 S5 K3; BUD-4-2, the visible "no approved budget" state).

``budget_state`` judges what the Consolidated Statement's Budget columns may
read for a (group, year): the submitted Approved Budget, the Budget Cycle it
names, the Cycle's input-fingerprint check (#348 S5-Q2 a: checked at every
read) and the Budget Layers on the site. One of five states, each with one
sentence that names the group, the year, the Cycle and what to do. A state
without a sentence raises: no state is ever shown blank.

Loaded by path; the module imports nothing from frappe or konsol.
"""
import ast
import importlib.util
import os
from datetime import datetime

import pytest

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PATH = os.path.join(APP_DIR, "close", "approved_budget_model.py")
_spec = importlib.util.spec_from_file_location("approved_budget_model_under_test", _PATH)
M = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(M)

GROUP, FY = "ZZGRP", 2027
CYCLE = "BCYC-ZZB-2027-abc12345"
APB = "APB-CG-ZZGRP--2027-0f0f0f0f"


def _declaration(**over):
    d = {"name": APB, "docstatus": 1, "consolidation_group": "CG-ZZGRP-", "fiscal_year": FY,
         "budget_cycle": CYCLE, "scenario_id": "ZZB", "rate_set": "ZZRS", "layer_policy": M.ALL_LAYERS,
         "layer": None, "input_fingerprint": "v1:aaaa", "owner": "lead@example.com",
         "modified": datetime(2026, 11, 17, 9, 30), "approved_by": "lead@example.com",
         "approved_on": datetime(2026, 11, 17, 9, 30)}
    d.update(over)
    return d


def _cycle(**over):
    c = {"name": CYCLE, "docstatus": 1, "scenario_id": "ZZB", "rate_set": "ZZRS", "input_fingerprint": "v1:aaaa"}
    c.update(over)
    return c


def _fingerprint(matches=True, stored="v1:aaaa", current="v1:aaaa"):
    return {"cycle": CYCLE, "docstatus": 1, "stored": stored, "current": current, "matches": matches,
            "message": "stub"}


LAYERS = [{"layer_code": "base", "sort_order": 1}, {"layer_code": "board", "sort_order": 2}]


def _state(declaration=..., cycle=..., fingerprint=..., layers=LAYERS):
    return M.budget_state(
        GROUP, FY,
        _declaration() if declaration is ... else declaration,
        _cycle() if cycle is ... else cycle,
        _fingerprint() if fingerprint is ... else fingerprint,
        layers)


# ---- every state, one fixture each -------------------------------------------------------

def test_ok_names_the_cycle_layers_rate_set_and_approver():
    out = _state()
    assert out["state"] == M.OK
    assert out["layers"] == ["base", "board"]
    msg = out["message"]
    for word in (GROUP, "FY2027", CYCLE, "ZZRS", "base, board", APB, "lead@example.com", "2026-11-17"):
        assert word in msg, (word, msg)


def test_the_approver_is_the_submitter_not_the_owner():
    """PR #554 review m1: the declaration's ``approved_by`` / ``approved_on``
    (stamped in before_submit) name the approval; the owner drafted it and
    ``modified`` moves with every save."""
    out = _state(declaration=_declaration(owner="drafter@example.com", modified=datetime(2026, 11, 20, 8, 0),
                                          approved_by="lead@example.com", approved_on=datetime(2026, 11, 17, 9, 30)))
    assert out["state"] == M.OK
    assert "approved by lead@example.com on 2026-11-17" in out["message"], out["message"]
    assert "drafter@example.com" not in out["message"] and "2026-11-20" not in out["message"]


def test_no_declaration_is_no_approved_budget_and_says_what_to_do():
    out = _state(declaration=None)
    assert out["state"] == M.NO_APPROVED_BUDGET
    assert out["layers"] is None
    assert "No approved budget for ZZGRP FY2027" in out["message"]
    assert "Approved Budget" in out["message"] and "declare" in out["message"].lower()


def test_a_draft_declaration_is_no_approved_budget_naming_the_draft():
    out = _state(declaration=_declaration(docstatus=0))
    assert out["state"] == M.NO_APPROVED_BUDGET
    assert APB in out["message"] and "draft" in out["message"].lower() and "submit" in out["message"].lower()


def test_a_cancelled_cycle_is_cycle_cancelled():
    out = _state(cycle=_cycle(docstatus=2), fingerprint=_fingerprint(matches=None))
    assert out["state"] == M.CYCLE_CANCELLED
    for word in (APB, GROUP, "FY2027", CYCLE, "cancelled", "amend"):
        assert word in out["message"], (word, out["message"])


def test_a_cycle_that_is_gone_or_unapproved_is_cycle_cancelled_too():
    out = _state(cycle=None, fingerprint=None)
    assert out["state"] == M.CYCLE_CANCELLED and "no longer exists" in out["message"]
    out = _state(cycle=_cycle(docstatus=0), fingerprint=_fingerprint(matches=None))
    assert out["state"] == M.CYCLE_CANCELLED and "not approved" in out["message"]


def test_changed_inputs_are_inputs_changed_naming_the_cycle():
    out = _state(fingerprint=_fingerprint(matches=False, current="v1:bbbb"))
    assert out["state"] == M.INPUTS_CHANGED
    for word in (APB, GROUP, "FY2027", CYCLE, "changed"):
        assert word in out["message"], (word, out["message"])
    assert out["layers"] is None, "nothing is read against changed inputs"


def test_a_declaration_stamped_from_another_fingerprint_is_inputs_changed():
    """Defensive: the declaration copied the Cycle's hash at submit; a Cycle
    whose stamped hash differs from the copy was not the one approved here."""
    out = _state(declaration=_declaration(input_fingerprint="v1:zzzz"))
    assert out["state"] == M.INPUTS_CHANGED


@pytest.mark.parametrize("blank", [None, ""])
def test_a_submitted_declaration_with_no_fingerprint_is_inputs_changed_never_ok(blank):
    """PR #554 review m2: a docstatus-1 declaration whose ``input_fingerprint``
    is blank cannot be checked against the Cycle (before_submit refuses the
    case, but a row written another way must not pass as ok). It is
    INPUTS_CHANGED, the sentence says it carries no fingerprint, and nothing
    is read."""
    out = _state(declaration=_declaration(input_fingerprint=blank))
    assert out["state"] == M.INPUTS_CHANGED
    assert out["layers"] is None
    assert "no input fingerprint" in out["message"], out["message"]
    for word in (APB, GROUP, "FY2027", CYCLE):
        assert word in out["message"], (word, out["message"])


def test_one_layer_that_no_longer_exists_is_layer_missing():
    out = _state(declaration=_declaration(layer_policy=M.ONE_LAYER, layer="gone"))
    assert out["state"] == M.LAYER_MISSING
    for word in (APB, GROUP, "FY2027", "gone", "Budget Layer"):
        assert word in out["message"], (word, out["message"])


def test_all_layers_with_no_layer_defined_is_layer_missing():
    out = _state(layers=[])
    assert out["state"] == M.LAYER_MISSING
    assert "no Budget Layer" in out["message"]


def test_one_layer_ok_reads_that_layer_only():
    out = _state(declaration=_declaration(layer_policy=M.ONE_LAYER, layer="board"))
    assert out["state"] == M.OK
    assert out["layers"] == ["board"]
    assert "layer board" in out["message"]


# ---- precedence --------------------------------------------------------------------------

def test_states_are_judged_in_order_declaration_cycle_inputs_layers():
    # No declaration wins over everything.
    assert _state(declaration=None, cycle=None, fingerprint=None, layers=[])["state"] == M.NO_APPROVED_BUDGET
    # A cancelled Cycle wins over changed inputs and missing layers.
    out = _state(cycle=_cycle(docstatus=2), fingerprint=_fingerprint(matches=False), layers=[])
    assert out["state"] == M.CYCLE_CANCELLED
    # Changed inputs win over missing layers.
    assert _state(fingerprint=_fingerprint(matches=False), layers=[])["state"] == M.INPUTS_CHANGED


# ---- layers_for ----------------------------------------------------------------------------

def test_layers_for_all_layers_is_every_layer_in_sort_order():
    rows = [{"layer_code": "board", "sort_order": 2}, {"layer_code": "base", "sort_order": 1}]
    assert M.layers_for(_declaration(), rows) == ["base", "board"]


def test_layers_for_one_layer_is_that_layer_when_it_exists_else_empty():
    assert M.layers_for(_declaration(layer_policy=M.ONE_LAYER, layer="board"), LAYERS) == ["board"]
    assert M.layers_for(_declaration(layer_policy=M.ONE_LAYER, layer="gone"), LAYERS) == []


def test_layers_for_refuses_an_unknown_policy():
    with pytest.raises(ValueError, match="Some layers"):
        M.layers_for(_declaration(layer_policy="Some layers"), LAYERS)


# ---- contracts -----------------------------------------------------------------------------

def test_every_state_has_a_sentence_and_an_unknown_state_raises():
    assert set(M.STATES) == {M.OK, M.NO_APPROVED_BUDGET, M.CYCLE_CANCELLED, M.INPUTS_CHANGED, M.LAYER_MISSING}
    for state in M.STATES:
        assert state in M.SENTENCES, state
    with pytest.raises(ValueError, match="bogus"):
        M.sentence("bogus", group=GROUP, fiscal_year=FY)


def test_a_missing_fingerprint_check_on_an_approved_cycle_raises_never_passes():
    """S5-Q2 (a): the check runs at every read; a reader that forgot it must
    not get an ok."""
    with pytest.raises(ValueError, match="fingerprint"):
        _state(fingerprint=None)


def test_module_imports_no_frappe():
    with open(_PATH) as fh:
        tree = ast.parse(fh.read())
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            assert not any(a.name.split(".")[0] in ("frappe", "konsol") for a in node.names)
        if isinstance(node, ast.ImportFrom):
            assert (node.module or "").split(".")[0] not in ("frappe", "konsol")

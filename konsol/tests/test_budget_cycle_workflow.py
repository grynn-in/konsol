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

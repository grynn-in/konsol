"""Rate Sheet's installed workflow (konsol#592 pair 1, T7).

Submit is the approval: a sheet is reviewed while it is a draft, and Approve is
the submit. The state lives in the doctype's own ``status`` field.
"""
import json
import os
import re

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATH = os.path.join(APP_DIR, "consolidation", "doctype", "rate_sheet", "rate_sheet_workflow.json")


def _workflow():
    assert os.path.exists(PATH), "no rate_sheet_workflow.json"
    with open(PATH) as f:
        return json.load(f)


def test_rate_sheet_is_installed():
    with open(os.path.join(APP_DIR, "workflows.py")) as f:
        installed = re.findall(r'"([^"]+)"', f.read().split("INSTALLED = (")[1].split(")")[0])
    assert "Rate Sheet" in installed


def test_names_doctype_and_state_field():
    wf = _workflow()
    assert wf["doctype"] == "Workflow"
    assert wf["document_type"] == "Rate Sheet"
    assert wf["name"] == wf["workflow_name"] == "Rate Sheet Workflow"
    assert wf["workflow_state_field"] == "status"
    assert wf["is_active"] == 1


def test_states_and_docstatus():
    wf = _workflow()
    assert {s["state"]: (str(s["doc_status"]), s["allow_edit"]) for s in wf["states"]} == {
        "Draft": ("0", "EPM Analyst"),
        "Pending Approval": ("0", "EPM Admin"),
        "Approved": ("1", "EPM Admin"),
        "Cancelled": ("2", "EPM Admin"),
    }
    assert wf["states"][0]["state"] == "Draft"


def test_transitions():
    got = {(t["state"], t["action"], t["next_state"], t["allowed"]) for t in _workflow()["transitions"]}
    assert got == {
        ("Draft", "Send for Approval", "Pending Approval", "EPM Analyst"),
        ("Pending Approval", "Reject", "Draft", "EPM Admin"),
        ("Pending Approval", "Approve", "Approved", "EPM Admin"),
    }
    assert all(t["allow_self_approval"] == 1 for t in _workflow()["transitions"])

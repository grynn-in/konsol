"""Build Approval is approved through a Frappe Workflow (konsol#215).

The approver role and self-approval live in the Workflow record, where a site
can change them; Frappe supplies the buttons and refuses a transition the
user's role does not have. These tests pin the shipped definition.
"""
import json
import os
import re

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOCTYPE_DIR = os.path.join(APP_DIR, "pipeline", "doctype", "build_approval")
WF_PATH = os.path.join(DOCTYPE_DIR, "build_approval_workflow.json")

STATES = ["Draft", "Pending Review", "Approved", "Running", "Completed", "Failed", "Cancelled"]
REQUESTERS = {"EPM Analyst", "EPM Admin", "System Manager", "Administrator"}
RERUNNERS = {"EPM Analyst", "EPM Admin", "System Manager"}


def _wf():
    with open(WF_PATH) as f:
        return json.load(f)


def _transitions(action):
    return [t for t in _wf()["transitions"] if t["action"] == action]


def test_header():
    wf = _wf()
    assert wf["doctype"] == "Workflow"
    assert wf["name"] == wf["workflow_name"] == "Build Approval Workflow"
    assert wf["document_type"] == "Build Approval"
    assert wf["workflow_state_field"] == "workflow_state"
    assert wf["is_active"] == 1
    assert wf["send_email_alert"] == 0
    assert wf["override_status"] == 0


def test_states_draft_first_all_draft_docstatus():
    states = _wf()["states"]
    assert [s["state"] for s in states] == STATES
    assert states[0]["state"] == "Draft", "Frappe allows a new document only the FIRST state"
    assert all(s["doc_status"] == "0" for s in states)
    edit = {s["state"]: s["allow_edit"] for s in states}
    assert edit == {
        "Draft": "EPM Analyst", "Pending Review": "EPM Admin", "Approved": "Administrator",
        "Running": "Administrator", "Completed": "EPM Analyst", "Failed": "EPM Analyst",
        "Cancelled": "EPM Analyst",
    }


def test_states_match_the_doctype_select():
    with open(os.path.join(DOCTYPE_DIR, "build_approval.json")) as f:
        fields = json.load(f)["fields"]
    field = next(f for f in fields if f["fieldname"] == "workflow_state")
    assert set(field["options"].split("\n")) - {""} == set(STATES)


def test_request_goes_by_risk_for_every_requester():
    rows = _transitions("Request")
    assert len(rows) == 8
    for role in REQUESTERS:
        mine = {(t["state"], t["next_state"], t["condition"]) for t in rows if t["allowed"] == role}
        assert mine == {
            ("Draft", "Approved", 'doc.risk_level == "low"'),
            ("Draft", "Pending Review", 'doc.risk_level == "high"'),
        }, role
    assert {t["allowed"] for t in rows} == REQUESTERS


def test_approve_and_reject_only_for_epm_admin():
    for action, nxt in (("Approve", "Approved"), ("Reject", "Cancelled")):
        rows = _transitions(action)
        assert [(t["state"], t["next_state"], t["allowed"]) for t in rows] == [
            ("Pending Review", nxt, "EPM Admin")], action
    out_of_review = [t for t in _wf()["transitions"] if t["state"] == "Pending Review"]
    assert {t["allowed"] for t in out_of_review} == {"EPM Admin"}


def test_system_transitions_only_for_administrator():
    expected = {
        ("Approved", "Start", "Running"),
        ("Approved", "Fail to Start", "Failed"),
        ("Running", "Complete", "Completed"),
        ("Running", "Fail", "Failed"),
    }
    system = [t for t in _wf()["transitions"] if t["state"] in ("Approved", "Running")]
    assert {(t["state"], t["action"], t["next_state"]) for t in system} == expected
    assert len(system) == 4
    assert all(t["allowed"] == "Administrator" for t in system)


def test_run_again_from_every_finished_state():
    rows = _transitions("Run Again")
    assert len(rows) == 9
    got = {(t["state"], t["next_state"], t["allowed"]) for t in rows}
    assert got == {(s, "Draft", r) for s in ("Completed", "Failed", "Cancelled") for r in RERUNNERS}


def test_every_transition_allows_self_approval_and_is_accounted_for():
    transitions = _wf()["transitions"]
    assert len(transitions) == 8 + 1 + 1 + 4 + 9
    assert all(t["allow_self_approval"] == 1 for t in transitions)
    assert {t["state"] for t in transitions} | {t["next_state"] for t in transitions} <= set(STATES)


def test_build_approval_is_installed():
    with open(os.path.join(APP_DIR, "workflows.py")) as f:
        src = f.read()
    installed = re.findall(r'"([^"]+)"', src.split("INSTALLED = (")[1].split(")")[0])
    assert "Build Approval" in installed
    doc = src.split('"""', 2)[1]
    assert "Build Approval" in doc and "Administrator" in doc, (
        "the docstring says the build job takes the system transitions as Administrator")

"""konsol#269 K6: the Cost Allocation Run doctype shape (plan section 2).

Pins the parent (submittable, workflow, autoname, title and search fields,
tabs and sections), its two read-only child tables, the workflow definition
and the INSTALLED entry. The controllers carry no behaviour yet: Compute
(K7) and the submit/cancel gates (K8) come later.
"""
import ast
import json
import os
import re

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EPM_DOCTYPES = os.path.join(APP_DIR, "epm", "doctype")
RUN = "cost_allocation_run"
WEIGHT = "cost_allocation_run_weight"
ACCOUNT = "cost_allocation_run_account"
LAYOUT_TYPES = {"Tab Break", "Section Break", "Column Break"}

# (fieldname, fieldtype, reqd, read_only) for every field that holds data.
RUN_FIELDS = [
    ("rule_set", "Link", 1, 0),
    ("entity", "Link", 0, 1),
    ("scenario", "Link", 1, 0),
    ("fiscal_year", "Int", 1, 0),
    ("fiscal_period", "Int", 1, 0),
    ("status", "Select", 0, 1),
    ("method", "Select", 0, 1),
    ("allocation_dimension", "Link", 0, 1),
    ("rule_set_modified", "Datetime", 0, 1),
    ("receiver_account_mode", "Select", 0, 1),
    ("allocation_account", "Link", 0, 1),
    ("amounts_after_approval", "Select", 0, 1),
    ("values_carried_forward", "Check", 0, 1),
    ("computed_at", "Datetime", 0, 1),
    ("computed_by", "Link", 0, 1),
    ("weights", "Table", 0, 1),
    ("accounts", "Table", 0, 1),
]

WEIGHT_FIELDS = [
    ("step", "Int"),
    ("sender_member", "Data"),
    ("receiver_member", "Data"),
    ("driver", "Link"),
    ("statistic", "Link"),
    ("value_period", "Int"),
    ("carried_forward", "Check"),
    ("driver_value", "Float"),
    ("weight", "Float"),
]

ACCOUNT_FIELDS = [
    ("main_account", "Link"),
    ("declared_account", "Link"),
]


def _doc(folder):
    with open(os.path.join(EPM_DOCTYPES, folder, folder + ".json")) as f:
        return json.load(f)


def _by_name(folder):
    return {f["fieldname"]: f for f in _doc(folder)["fields"]}


def _workflow():
    with open(os.path.join(EPM_DOCTYPES, RUN, "cost_allocation_run_workflow.json")) as f:
        return json.load(f)


def test_doctype_folders_are_packages_with_json_and_controller():
    for folder in (RUN, WEIGHT, ACCOUNT):
        base = os.path.join(EPM_DOCTYPES, folder)
        for name in ("__init__.py", folder + ".json", folder + ".py",):
            assert os.path.exists(os.path.join(base, name)), f"{folder}/{name} missing"


def test_doctype_header_is_a_submittable_epm_doctype():
    doc = _doc(RUN)
    assert doc["name"] == "Cost Allocation Run"
    assert doc["doctype"] == "DocType"
    assert doc["module"] == "EPM"
    assert doc["is_submittable"] == 1
    assert doc.get("istable", 0) == 0
    assert doc["track_changes"] == 1
    assert doc["autoname"] == "format:CAR-{#####}"
    assert doc["title_field"] == "rule_set"
    assert doc["search_fields"] == "scenario,fiscal_year,fiscal_period"


def test_doctype_tabs_and_sections_follow_the_plan():
    doc = _doc(RUN)
    tabs = [f["label"] for f in doc["fields"] if f["fieldtype"] == "Tab Break"]
    sections = [f["label"] for f in doc["fields"] if f["fieldtype"] == "Section Break"]
    assert tabs == ["Run", "Weights", "Accounts"]
    assert sections == ["Scope", "Computation", "Computed", "Weights", "Accounts"]


def test_doctype_run_fields_types_required_and_read_only():
    doc = _doc(RUN)
    got = [
        (f["fieldname"], f["fieldtype"], f.get("reqd", 0), f.get("read_only", 0))
        for f in doc["fields"] if f["fieldtype"] not in LAYOUT_TYPES
    ]
    assert got == RUN_FIELDS


def test_doctype_run_link_and_select_options():
    fields = _by_name(RUN)
    assert fields["rule_set"]["options"] == "Cost Allocation Rule Set"
    assert fields["entity"]["options"] == "Entity"
    assert fields["entity"]["fetch_from"] == "rule_set.entity"
    assert fields["scenario"]["options"] == "Scenario"
    assert fields["allocation_dimension"]["options"] == "Dimension"
    assert fields["allocation_account"]["options"] == "Main Account"
    assert fields["computed_by"]["options"] == "User"
    assert fields["weights"]["options"] == "Cost Allocation Run Weight"
    assert fields["accounts"]["options"] == "Cost Allocation Run Account"
    assert fields["status"]["options"] == "Draft\nPending Approval\nApproved\nCancelled"
    assert fields["status"]["default"] == "Draft"
    assert fields["method"]["options"] == "Direct\nStep-Down"
    assert fields["receiver_account_mode"]["options"] == (
        "Sender's Own Accounts\nDeclared Allocation Account")
    assert fields["amounts_after_approval"]["options"] == "Follow Latest Data\nFreeze at Approval"


def test_doctype_declared_account_is_shown_only_when_declared():
    fields = _by_name(RUN)
    assert fields["allocation_account"]["depends_on"] == (
        "eval:doc.receiver_account_mode=='Declared Allocation Account'")


def test_doctype_non_obvious_fields_carry_help_text():
    fields = _by_name(RUN)
    for name in ("entity", "fiscal_period", "rule_set_modified", "receiver_account_mode",
                 "allocation_account", "amounts_after_approval", "values_carried_forward"):
        assert fields[name].get("description"), f"{name} has no help text"


def test_doctype_child_tables_are_istable_and_all_read_only():
    names = {WEIGHT: "Cost Allocation Run Weight", ACCOUNT: "Cost Allocation Run Account"}
    for folder, expected in ((WEIGHT, WEIGHT_FIELDS), (ACCOUNT, ACCOUNT_FIELDS)):
        doc = _doc(folder)
        assert doc["name"] == names[folder], folder
        assert doc["istable"] == 1, folder
        assert doc["module"] == "EPM", folder
        fields = doc["fields"]
        assert [(f["fieldname"], f["fieldtype"]) for f in fields] == expected, folder
        assert all(f.get("read_only") == 1 for f in fields), (
            folder, [f["fieldname"] for f in fields if f.get("read_only") != 1])


def test_doctype_weight_child_links_and_precision():
    fields = _by_name(WEIGHT)
    assert fields["driver"]["options"] == "Cost Allocation Driver"
    assert fields["statistic"]["options"] == "Cost Allocation Statistic"
    assert fields["weight"]["precision"] == "9"
    assert fields["driver_value"]["precision"] == "9"


def test_doctype_account_child_links():
    fields = _by_name(ACCOUNT)
    assert fields["main_account"]["options"] == "Main Account"
    assert fields["declared_account"]["options"] == "Main Account"


def test_doctype_child_controllers_define_their_classes():
    for folder, cls in ((RUN, "CostAllocationRun"), (WEIGHT, "CostAllocationRunWeight"),
                        (ACCOUNT, "CostAllocationRunAccount")):
        with open(os.path.join(EPM_DOCTYPES, folder, folder + ".py")) as f:
            names = [n.name for n in ast.parse(f.read()).body if isinstance(n, ast.ClassDef)]
        assert names == [cls], (folder, names)


def test_doctype_workflow_states_and_transitions():
    wf = _workflow()
    assert wf["document_type"] == "Cost Allocation Run"
    assert wf["workflow_state_field"] == "status"
    assert wf["is_active"] == 1
    states = {s["state"]: (int(s["doc_status"]), s["allow_edit"]) for s in wf["states"]}
    assert states == {
        "Draft": (0, "EPM Analyst"),
        "Pending Approval": (0, "EPM Admin"),
        "Approved": (1, "EPM Admin"),
        "Cancelled": (2, "EPM Admin"),
    }
    transitions = [(t["state"], t["action"], t["next_state"], t["allowed"]) for t in wf["transitions"]]
    assert transitions == [
        ("Draft", "Send for Approval", "Pending Approval", "EPM Analyst"),
        ("Pending Approval", "Reject", "Draft", "EPM Admin"),
        ("Pending Approval", "Approve", "Approved", "EPM Admin"),
        ("Approved", "Cancel", "Cancelled", "EPM Admin"),
    ]


def test_doctype_approver_holds_submit_and_cancel():
    perms = {p["role"]: p for p in _doc(RUN)["permissions"]}
    assert perms["EPM Admin"].get("submit") == 1
    assert perms["EPM Admin"].get("cancel") == 1
    assert not perms["EPM Analyst"].get("submit")


def test_doctype_is_installed_as_a_workflow():
    with open(os.path.join(APP_DIR, "workflows.py")) as f:
        installed = re.findall(r'"([^"]+)"', f.read().split("INSTALLED = (")[1].split(")")[0])
    assert "Cost Allocation Run" in installed

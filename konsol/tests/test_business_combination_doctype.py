"""Business Combination doctypes (konsolidat#198, design 2a): the deal is a
declared input, the policy lives on the Consolidation Group root, and only the
accounting mechanics are programmed. These tests pin the JSON shape (parent,
three child tables, workflow) — the controller comes in a later row.
"""
import json
import os
import re

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOCTYPE_DIR = os.path.join(APP_DIR, "consolidation", "doctype")

CHILDREN = {
    "consideration": ("business_combination_consideration", "Business Combination Consideration"),
    "acquired_balances": ("business_combination_acquired_balance", "Business Combination Acquired Balance"),
    "costs": ("business_combination_cost", "Business Combination Cost"),
}


def _json(folder, filename=None):
    path = os.path.join(DOCTYPE_DIR, folder, (filename or folder) + ".json")
    with open(path) as f:
        return json.load(f)


def _fields(doc):
    return {f["fieldname"]: f for f in doc["fields"]}


def _parent():
    return _json("business_combination")


def test_every_doctype_folder_is_a_package_with_json_and_controller():
    for folder in ["business_combination"] + [c[0] for c in CHILDREN.values()]:
        base = os.path.join(DOCTYPE_DIR, folder)
        for name in ("__init__.py", folder + ".json", folder + ".py"):
            assert os.path.exists(os.path.join(base, name)), f"{folder}/{name} missing"


def test_business_combination_header_shape():
    doc = _parent()
    assert doc["name"] == "Business Combination"
    assert doc["doctype"] == "DocType"
    assert doc["module"] == "Consolidation"
    assert doc["is_submittable"] == 1
    assert doc.get("istable", 0) == 0
    assert doc["autoname"] == "format:BC-{consolidation_group}-{acquired_entity}-{acquisition_date}"
    assert doc["title_field"] == "acquired_entity"
    assert doc["track_changes"] == 1


def test_deal_tab_carries_the_declared_inputs():
    fields = _fields(_parent())
    assert fields["tab_deal"]["fieldtype"] == "Tab Break"
    assert fields["tab_deal"]["label"] == "Deal"
    expected = {
        "consolidation_group": ("Data", None),
        "acquired_entity": ("Link", "Entity"),
        "acquisition_date": ("Date", None),
        "share_acquired_pct": ("Percent", None),
        "consideration_currency": ("Link", "ISO Currency"),
    }
    for fn, (ftype, options) in expected.items():
        assert fields[fn]["fieldtype"] == ftype, fn
        assert fields[fn].get("reqd") == 1, f"{fn} is always required"
        if options:
            assert fields[fn]["options"] == options, fn
    # Copied from the group root / set on submit: never typed by hand.
    assert fields["nci_measurement"]["fieldtype"] == "Data"
    assert fields["nci_measurement"]["read_only"] == 1
    assert fields["nci_measurement"].get("reqd", 0) == 0
    assert fields["ownership_period"]["fieldtype"] == "Link"
    assert fields["ownership_period"]["options"] == "Ownership Period"
    assert fields["ownership_period"]["read_only"] == 1


def test_nci_measurement_is_elected_per_deal_with_the_group_as_default():
    """konsol#205: IFRS 3.19 lets each business combination elect how NCI is
    measured; blank takes the group's NCI Measurement."""
    doc = _parent()
    fields = _fields(doc)
    override = fields["nci_measurement_override"]
    assert override["fieldtype"] == "Select"
    assert override["options"] == "\npartial\nfull"
    assert override["label"] == "NCI Measurement for This Deal"
    assert override["description"] == (
        "Leave blank to use the group's NCI Measurement. IFRS 3.19 lets each business "
        "combination elect; US GAAP requires full.")
    assert override.get("reqd", 0) == 0 and override.get("read_only", 0) == 0
    assert "default" not in override, "blank means the group's value, not a default"
    in_force = fields["nci_measurement"]
    assert in_force["read_only"] == 1
    assert in_force["description"] == (
        "The NCI measurement in force for this deal: the override when set, otherwise the group's.")
    order = [f["fieldname"] for f in doc["fields"]]
    assert abs(order.index("nci_measurement_override") - order.index("nci_measurement")) == 1


def test_table_fields_point_at_the_three_children():
    fields = _fields(_parent())
    for fn, (_, child_name) in CHILDREN.items():
        assert fields[fn]["fieldtype"] == "Table", fn
        assert fields[fn]["options"] == child_name, fn
    assert fields["consideration"]["reqd"] == 1, "a deal has at least one consideration line"
    assert fields["acquired_balances"].get("reqd", 0) == 0, \
        "an empty table is refused by validate(), not by reqd"
    description = fields["acquired_balances"]["description"]
    assert "otherwise the trial balance is used" not in description, (
        "konsol#206: the Acquired Balance Sheet is always required")
    assert description == (
        "The entity's balance sheet at acquisition with fair value adjustments per line, "
        "in the acquired entity's Functional Currency (translated to the group currency at "
        "the acquisition period's closing rate). Always required: enter the acquisition-date "
        "balances, or use Get Balances from Trial Balance.")
    assert fields["costs"].get("reqd", 0) == 0


def test_acquired_balance_sheet_carries_the_fva_total_and_the_source_above_the_table():
    """konsol#207: the purchase price allocation's total step-up is declared on
    the deal; Get Balances from Trial Balance places it on the lines and
    records where the lines came from."""
    doc = _parent()
    fields = _fields(doc)
    total = fields["fair_value_adjustment_total"]
    assert total["fieldtype"] == "Currency"
    assert total["label"] == "Fair Value Adjustment Total"
    assert total["description"] == (
        "The purchase price allocation's total step-up from book to fair value, in the acquired "
        "entity's currency. Get Balances from Trial Balance places it on the lines; the lines must "
        "add up to it.")
    assert total.get("reqd", 0) == 0 and total.get("read_only", 0) == 0
    source = fields["balance_sheet_source"]
    assert source["fieldtype"] == "Small Text"
    assert source["label"] == "Balance Sheet Source"
    assert source["read_only"] == 1 and source["no_copy"] == 1
    order = [f["fieldname"] for f in doc["fields"]]
    section, table = order.index("acquired_balances_section"), order.index("acquired_balances")
    assert section < order.index("fair_value_adjustment_total") < table
    assert section < order.index("balance_sheet_source") < table


def test_form_button_gets_balances_from_the_trial_balance():
    path = os.path.join(DOCTYPE_DIR, "business_combination", "business_combination.js")
    with open(path) as f:
        src = f.read()
    assert 'frappe.ui.form.on("Business Combination"' in src
    assert "refresh(frm)" in src
    assert "frm.doc.docstatus === 0 && !frm.is_new()" in src
    assert '__("Get Balances from Trial Balance")' in src
    assert "frm.add_custom_button(" in src
    assert "frappe.confirm(" in src
    assert "This replaces the {0} lines of the Acquired Balance Sheet. Continue?" in src
    assert ('"konsol.consolidation.doctype.business_combination.business_combination.'
            'get_balances_from_trial_balance"') in src
    assert "name: frm.doc.name" in src
    assert "frm.reload_doc()" in src
    # PR #209 review 1: only a real Draft (Pending Approval is docstatus 0 too).
    assert 'frm.doc.status === "Draft"' in src
    # PR #209 review 5: unsaved edits (a typed Fair Value Adjustment Total) are
    # saved first, and the method runs after the save resolves.
    assert "frm.is_dirty()" in src
    assert "frm.save().then(run)" in src


def test_result_tab_is_computed_and_read_only():
    fields = _fields(_parent())
    assert fields["tab_result"]["fieldtype"] == "Tab Break"
    assert fields["tab_result"]["label"] == "Result"
    for fn in ("total_consideration", "net_assets_acquired", "fair_value_adjustments", "goodwill",
               "bargain_purchase_gain", "nci_at_acquisition", "costs_expensed"):
        assert fields[fn]["fieldtype"] == "Currency", fn
        assert fields[fn]["read_only"] == 1, f"{fn} is computed on validate"
        assert fields[fn].get("reqd", 0) == 0, fn
        assert "default" not in fields[fn], f"{fn}: no defaults, the result is computed"


def test_status_is_the_workflow_state_and_the_document_can_be_amended():
    doc = _parent()
    fields = _fields(doc)
    assert fields["status"]["fieldtype"] == "Select"
    assert fields["status"]["options"] == "Draft\nPending Approval\nApproved\nCancelled"
    assert fields["status"]["read_only"] == 1
    assert fields["description"]["fieldtype"] == "Small Text"
    assert fields["amended_from"]["fieldtype"] == "Link"
    assert fields["amended_from"]["options"] == "Business Combination"
    assert fields["amended_from"]["read_only"] == 1
    assert fields["amended_from"]["no_copy"] == 1
    roles = {p["role"]: p for p in doc["permissions"]}
    assert roles["EPM Admin"]["submit"] == 1 and roles["EPM Admin"]["cancel"] == 1
    assert roles["EPM Analyst"]["create"] == 1 and roles["EPM Analyst"].get("submit", 0) == 0
    assert roles["EPM User"].get("write", 0) == 0


def test_children_are_child_tables_in_the_consolidation_module():
    for fn, (folder, child_name) in CHILDREN.items():
        doc = _json(folder)
        assert doc["name"] == child_name
        assert doc["module"] == "Consolidation"
        assert doc["istable"] == 1, child_name
        assert doc.get("is_submittable", 0) == 0, child_name
        assert doc["editable_grid"] == 1, child_name


def test_consideration_child_fields():
    fields = _fields(_json("business_combination_consideration"))
    assert fields["component"]["fieldtype"] == "Select"
    assert fields["component"]["options"] == (
        "Cash\nDeferred\nContingent\nEquity instruments\nPreviously held interest\nOther")
    assert fields["component"]["reqd"] == 1
    assert fields["amount"]["fieldtype"] == "Currency" and fields["amount"]["reqd"] == 1
    assert fields["currency"]["fieldtype"] == "Link" and fields["currency"]["options"] == "ISO Currency"
    assert fields["currency"]["reqd"] == 1
    assert fields["settlement_date"]["fieldtype"] == "Date" and fields["settlement_date"].get("reqd", 0) == 0
    assert fields["description"]["fieldtype"] == "Data" and fields["description"].get("reqd", 0) == 0


def test_acquired_balance_child_fields():
    fields = _fields(_json("business_combination_acquired_balance"))
    assert fields["main_account"]["fieldtype"] == "Link"
    assert fields["main_account"]["options"] == "Main Account"
    assert fields["main_account"]["reqd"] == 1
    assert fields["book_amount"]["fieldtype"] == "Currency" and fields["book_amount"]["reqd"] == 1
    assert fields["fair_value_adjustment"]["fieldtype"] == "Currency"
    assert fields["fair_value_adjustment"].get("reqd", 0) == 0
    assert fields["note"]["fieldtype"] == "Data" and fields["note"].get("reqd", 0) == 0


def test_cost_child_fields():
    fields = _fields(_json("business_combination_cost"))
    assert fields["kind"]["fieldtype"] == "Select"
    assert fields["kind"]["options"] == "Legal\nAdvisory\nDue diligence\nOther"
    assert fields["kind"]["reqd"] == 1
    assert fields["amount"]["fieldtype"] == "Currency" and fields["amount"]["reqd"] == 1
    assert fields["currency"]["fieldtype"] == "Link" and fields["currency"]["options"] == "ISO Currency"
    assert fields["currency"]["reqd"] == 1
    assert fields["description"]["fieldtype"] == "Data" and fields["description"].get("reqd", 0) == 0


def test_no_policy_lives_on_the_deal():
    """Policy is configured once on the group root (design 1a); the deal only
    carries a read-only copy of the NCI measurement it was computed under."""
    fields = _fields(_parent())
    for policy_field in ("accounting_framework", "goodwill_treatment", "acquisition_costs_treatment",
                         "measurement_period", "bargain_purchase", "goodwill_method"):
        assert policy_field not in fields, policy_field
    for f in fields.values():
        assert "default" not in f or f["fieldname"] == "status", f["fieldname"]


def test_controllers_are_empty_documents_for_now():
    for folder, cls in (("business_combination", "BusinessCombination"),
                        ("business_combination_consideration", "BusinessCombinationConsideration"),
                        ("business_combination_acquired_balance", "BusinessCombinationAcquiredBalance"),
                        ("business_combination_cost", "BusinessCombinationCost")):
        with open(os.path.join(DOCTYPE_DIR, folder, folder + ".py")) as f:
            src = f.read()
        assert "from frappe.model.document import Document" in src, folder
        assert f"class {cls}(Document)" in src, folder


def test_workflow_copies_the_approval_shape():
    wf = _json("business_combination", "business_combination_workflow")
    assert wf["doctype"] == "Workflow"
    assert wf["document_type"] == "Business Combination"
    assert wf["workflow_name"] == "Business Combination Workflow"
    assert wf["is_active"] == 1
    assert wf["workflow_state_field"] == "status"
    states = {s["state"]: s for s in wf["states"]}
    assert set(states) == {"Draft", "Pending Approval", "Approved", "Cancelled"}
    assert states["Draft"]["doc_status"] == "0" and states["Draft"]["allow_edit"] == "EPM Analyst"
    assert states["Pending Approval"]["doc_status"] == "0" and states["Pending Approval"]["allow_edit"] == "EPM Admin"
    assert states["Approved"]["doc_status"] == "1" and states["Approved"]["allow_edit"] == "EPM Admin"
    transitions = {(t["state"], t["action"]): t for t in wf["transitions"]}
    assert set(transitions) == {("Draft", "Send for Approval"), ("Pending Approval", "Reject"),
                                ("Pending Approval", "Approve")}
    assert transitions[("Draft", "Send for Approval")]["next_state"] == "Pending Approval"
    assert transitions[("Draft", "Send for Approval")]["allowed"] == "EPM Analyst"
    assert transitions[("Pending Approval", "Reject")]["next_state"] == "Draft"
    assert transitions[("Pending Approval", "Reject")]["allowed"] == "EPM Admin"
    assert transitions[("Pending Approval", "Approve")]["next_state"] == "Approved"
    assert transitions[("Pending Approval", "Approve")]["allowed"] == "EPM Admin"


def test_a_cancelled_deal_is_marked_cancelled():
    """Frappe's `set_workflow_state_on_action` writes the state whose
    `doc_status` is "2" when a document is cancelled; without one the deal
    keeps saying "Approved" after its cancel (PR #202 finding 2). Cancel drives
    the state, so no transition leads into it."""
    wf = _json("business_combination", "business_combination_workflow")
    cancelled = [s for s in wf["states"] if s["doc_status"] == "2"]
    assert len(cancelled) == 1, "exactly one cancelled state"
    assert cancelled[0]["state"] == "Cancelled"
    assert cancelled[0]["allow_edit"] == "EPM Admin"
    assert not [t for t in wf["transitions"] if t["next_state"] == "Cancelled"], "cancel is not a transition"
    assert "Cancelled" in _fields(_parent())["status"]["options"].split("\n")


def test_the_workflow_is_installed():
    with open(os.path.join(APP_DIR, "workflows.py")) as f:
        src = f.read()
    installed = re.findall(r'"([^"]+)"', src.split("INSTALLED = (")[1].split(")")[0])
    assert "Business Combination" in installed
    assert "Consolidation Adjustment" in installed, "the existing entry stays"

"""K3 (konsol#269): the Cost Allocation Driver master doctype.

A driver names the statistic a cost pool is spread on (headcount, floor area).
It names a Dimension, and that Dimension must be Published. The controller
refuses an unpublished dimension at save time.

Pure JSON-reading tests plus a controller test with a stubbed frappe, so they
run on the host without a bench.
"""
import importlib.util
import json
import os
import sys
import types

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOCTYPE_DIR = os.path.join(APP_DIR, "epm", "doctype", "cost_allocation_driver")
DRIVER_JSON = os.path.join(DOCTYPE_DIR, "cost_allocation_driver.json")
DRIVER_PY = os.path.join(DOCTYPE_DIR, "cost_allocation_driver.py")

CARRY_FORWARD_HELP = (
    "When a period has no value for a receiver, use its latest earlier period's "
    "value in the same scenario. Every carried value is reported on the run. "
    "Off: a missing value refuses the run.")


def _load_meta():
    with open(DRIVER_JSON) as f:
        return json.load(f)


def _fields():
    return _load_meta()["fields"]


def _field(fieldname):
    for field in _fields():
        if field.get("fieldname") == fieldname:
            return field
    raise AssertionError(f"Cost Allocation Driver has no field {fieldname!r}")


def test_driver_doctype_identity():
    meta = _load_meta()
    assert meta["name"] == "Cost Allocation Driver"
    assert meta["doctype"] == "DocType"
    assert meta["module"] == "EPM"
    assert meta["issingle"] == 0
    assert meta["istable"] == 0
    assert meta["autoname"] == "field:driver_name"
    assert meta["title_field"] == "driver_name"
    assert meta["track_changes"] == 1


def test_driver_layout_tab_section_then_fields():
    order = [(f["fieldtype"], f["fieldname"]) for f in _fields()]
    assert order == [
        ("Tab Break", "driver_tab"),
        ("Section Break", "driver_section"),
        ("Data", "driver_name"),
        ("Link", "dimension"),
        ("Data", "unit"),
        ("Column Break", "col_break_1"),
        ("Check", "carry_forward"),
        ("Check", "is_active"),
        ("Small Text", "description"),
    ], f"layout drifted: {order}"


def test_driver_driver_name_is_required_unique_data():
    field = _field("driver_name")
    assert field["fieldtype"] == "Data"
    assert field["label"] == "Driver Name"
    assert field["reqd"] == 1
    assert field["unique"] == 1


def test_driver_dimension_is_required_link_to_dimension_with_published_help():
    field = _field("dimension")
    assert field["fieldtype"] == "Link"
    assert field["options"] == "Dimension"
    assert field["label"] == "Dimension"
    assert field["reqd"] == 1
    assert field["description"].startswith("Must be Published."), field["description"]


def test_driver_unit_is_optional_data():
    field = _field("unit")
    assert field["fieldtype"] == "Data"
    assert field["label"] == "Unit"
    assert not field.get("reqd")


def test_driver_carry_forward_is_check_with_exact_help():
    field = _field("carry_forward")
    assert field["fieldtype"] == "Check"
    assert field["label"] == "Carry Forward"
    assert field["description"] == CARRY_FORWARD_HELP
    assert not field.get("reqd")
    assert not field.get("default"), "carry_forward must default off"


def test_driver_is_active_is_check_defaulting_on():
    field = _field("is_active")
    assert field["fieldtype"] == "Check"
    assert field["label"] == "Active"
    assert str(field.get("default")) == "1"


def test_driver_description_is_small_text():
    field = _field("description")
    assert field["fieldtype"] == "Small Text"
    assert field["label"] == "Description"
    assert not field.get("reqd")


def test_driver_permissions_match_dimension_siblings():
    perms = {p["role"]: p for p in _load_meta()["permissions"]}
    assert set(perms) == {"System Manager", "EPM Admin"}
    for role in perms:
        for right in ("read", "write", "create", "delete"):
            assert perms[role].get(right) == 1, f"{role} lacks {right}"


# --- controller: refuses an unpublished dimension ---------------------------

class _Refused(Exception):
    pass


class _FakeFrappe:
    """Stands in for frappe: a dimension status table and a throw."""

    def __init__(self, statuses):
        self.statuses = statuses
        self.db = self
        self.lookups = []

    def get_value(self, doctype, name, fieldname):
        self.lookups.append((doctype, name, fieldname))
        assert doctype == "Dimension" and fieldname == "status"
        return self.statuses.get(name)

    def throw(self, message, *args, **kwargs):
        raise _Refused(message)


def _install_document_stub():
    """Give the controller a Document base without a bench."""
    if "frappe.model.document" in sys.modules:
        return
    class Document:
        pass
    model = types.ModuleType("frappe.model")
    document = types.ModuleType("frappe.model.document")
    document.Document = Document
    sys.modules.setdefault("frappe", types.ModuleType("frappe"))
    sys.modules["frappe.model"] = model
    sys.modules["frappe.model.document"] = document


def _load_controller(frappe_stub):
    _install_document_stub()
    spec = importlib.util.spec_from_file_location("cost_allocation_driver_k3", DRIVER_PY)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.frappe = frappe_stub
    return module.CostAllocationDriver


def _driver(controller_cls, dimension):
    doc = controller_cls.__new__(controller_cls)
    doc.driver_name = "ZZ Headcount"
    doc.dimension = dimension
    return doc


def test_driver_controller_refuses_draft_dimension():
    fake = _FakeFrappe({"dim_zz_draft": "Draft"})
    doc = _driver(_load_controller(fake), "dim_zz_draft")
    try:
        doc.validate()
    except _Refused as exc:
        assert "Published" in str(exc)
    else:
        raise AssertionError("a Draft dimension was accepted")
    assert fake.lookups == [("Dimension", "dim_zz_draft", "status")]


def test_driver_controller_refuses_inactive_dimension():
    fake = _FakeFrappe({"dim_zz_old": "Inactive"})
    doc = _driver(_load_controller(fake), "dim_zz_old")
    try:
        doc.validate()
    except _Refused:
        pass
    else:
        raise AssertionError("an Inactive dimension was accepted")


def test_driver_controller_refuses_missing_dimension():
    fake = _FakeFrappe({})
    doc = _driver(_load_controller(fake), "dim_zz_missing")
    try:
        doc.validate()
    except _Refused:
        pass
    else:
        raise AssertionError("a dimension that does not exist was accepted")


def test_driver_controller_accepts_published_dimension():
    fake = _FakeFrappe({"dim_zz_pub": "Published"})
    doc = _driver(_load_controller(fake), "dim_zz_pub")
    doc.validate()


# === K4 (konsol#269): Cost Allocation Rule Set and its child doctypes ========

EPM_DIR = os.path.join(APP_DIR, "epm", "doctype")
RS_DIR = os.path.join(EPM_DIR, "cost_allocation_rule_set")
RS_PY = os.path.join(RS_DIR, "cost_allocation_rule_set.py")
DECLARED_EVAL = "eval:doc.receiver_account_mode=='Declared Allocation Account'"
NOT_ALL_EVAL = "eval:!doc.applies_to_all_scenarios"
FROZEN_HELP = (
    "Frozen amounts come from the first allocation build after approval; "
    "drift from current data is reported.")


def _doctype_json(folder):
    with open(os.path.join(EPM_DIR, folder, folder + ".json")) as f:
        return json.load(f)


def _rs_field(fieldname):
    for field in _doctype_json("cost_allocation_rule_set")["fields"]:
        if field.get("fieldname") == fieldname:
            return field
    raise AssertionError(f"Cost Allocation Rule Set has no field {fieldname!r}")


def _child_fields(folder):
    return {f["fieldname"]: f for f in _doctype_json(folder)["fields"]
            if f["fieldtype"] not in ("Section Break", "Column Break", "Tab Break")}


def test_rule_set_doctype_identity():
    meta = _doctype_json("cost_allocation_rule_set")
    assert meta["name"] == "Cost Allocation Rule Set"
    assert meta["module"] == "EPM"
    assert meta["istable"] == 0
    assert meta["track_changes"] == 1
    assert meta["search_fields"] == "entity,method"
    assert os.path.exists(os.path.join(RS_DIR, "cost_allocation_rule_set_list.js"))


def test_rule_set_layout_has_tabs_and_labelled_sections():
    types_ = [f["fieldtype"] for f in _doctype_json("cost_allocation_rule_set")["fields"]]
    assert types_.count("Tab Break") == 2
    for f in _doctype_json("cost_allocation_rule_set")["fields"]:
        if f["fieldtype"] in ("Tab Break", "Section Break"):
            assert f.get("label"), f
    assert _doctype_json("cost_allocation_rule_set")["fields"][0]["fieldtype"] == "Tab Break"


def test_rule_set_scope_fields():
    f = _rs_field("rule_set_name")
    assert f["fieldtype"] == "Data" and f["reqd"] == 1
    f = _rs_field("entity")
    assert (f["fieldtype"], f["options"], f["reqd"]) == ("Link", "Entity", 1)
    f = _rs_field("allocation_dimension")
    assert (f["fieldtype"], f["options"], f["reqd"]) == ("Link", "Dimension", 1)
    assert "Published" in f["description"] and "In Budget" in f["description"]
    assert _rs_field("column")["fieldtype"] in ("Column Break", "Data")
    f = _rs_field("method")
    assert (f["fieldtype"], f["options"], f["reqd"]) == ("Select", "Direct\nStep-Down", 1)
    f = _rs_field("status")
    assert (f["fieldtype"], f["options"], f["reqd"]) == ("Select", "Draft\nActive\nInactive", 1)


def test_rule_set_select_options_equal_model_constants():
    from konsol import cost_allocation_model as m
    assert _rs_field("method")["options"].split("\n") == list(m.METHODS)
    assert _rs_field("receiver_account_mode")["options"].split("\n") == list(m.ACCOUNT_MODES)


def test_rule_set_treatment_fields():
    f = _rs_field("receiver_account_mode")
    assert f["fieldtype"] == "Select" and f["reqd"] == 1
    f = _rs_field("allocation_account")
    assert (f["fieldtype"], f["options"]) == ("Link", "Main Account")
    assert f["depends_on"] == DECLARED_EVAL
    assert f["mandatory_depends_on"] == DECLARED_EVAL
    assert not f.get("reqd")
    f = _rs_field("amounts_after_approval")
    assert f["fieldtype"] == "Select" and f["reqd"] == 1
    assert f["options"] == "Follow Latest Data\nFreeze at Approval"
    assert f["description"] == FROZEN_HELP


def test_rule_set_scenario_and_table_fields():
    assert _rs_field("applies_to_all_scenarios")["fieldtype"] == "Check"
    f = _rs_field("scenarios")
    assert (f["fieldtype"], f["options"]) == ("Table", "Cost Allocation Rule Set Scenario")
    assert f["mandatory_depends_on"] == NOT_ALL_EVAL
    assert f["depends_on"] == NOT_ALL_EVAL
    f = _rs_field("accounts")
    assert (f["fieldtype"], f["options"], f["reqd"]) == ("Table", "Cost Allocation Rule Set Account", 1)
    assert "descendants" in f["description"]
    f = _rs_field("rules")
    assert (f["fieldtype"], f["options"], f["reqd"]) == ("Table", "Cost Allocation Rule", 1)
    f = _rs_field("receivers")
    assert (f["fieldtype"], f["options"]) == ("Table", "Cost Allocation Rule Receiver")


def test_rule_set_child_doctypes():
    expect = {
        "cost_allocation_rule_set_scenario": {"scenario": ("Link", "Scenario", 1)},
        "cost_allocation_rule_set_account": {"main_account": ("Link", "Main Account", 1),
                                             "account_name": ("Data", None, 0)},
        "cost_allocation_rule": {"step": ("Int", None, 1), "sender_member": ("Data", None, 1),
                                 "driver": ("Link", "Cost Allocation Driver", 1),
                                 "description": ("Data", None, 0)},
        "cost_allocation_rule_receiver": {"step": ("Int", None, 1),
                                          "receiver_member": ("Data", None, 1)},
    }
    for folder, fields in expect.items():
        meta = _doctype_json(folder)
        assert meta["istable"] == 1 and meta["module"] == "EPM", folder
        assert meta["name"] == folder.replace("_", " ").title(), folder
        got = _child_fields(folder)
        assert set(got) == set(fields), (folder, set(got))
        for name, (ft, opts, reqd) in fields.items():
            assert got[name]["fieldtype"] == ft, (folder, name)
            assert got[name].get("options") == opts, (folder, name)
            assert bool(got[name].get("reqd")) == bool(reqd), (folder, name)
        assert os.path.exists(os.path.join(EPM_DIR, folder, folder + ".py"))
    acct = _child_fields("cost_allocation_rule_set_account")["account_name"]
    assert acct["fetch_from"] == "main_account.account_name" and acct["read_only"] == 1


# --- controller -------------------------------------------------------------

class _Row:
    def __init__(self, **kw):
        self.__dict__.update(kw)

    def as_dict(self):
        return dict(self.__dict__)


class _ThrowingFrappe:
    def throw(self, message, *args, **kwargs):
        raise _Refused(message)


def _rs_module(state=("Published", 1), members=("A", "B", "C"), drivers=None):
    _install_document_stub()
    spec = importlib.util.spec_from_file_location("cost_allocation_rule_set_k4", RS_PY)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.frappe = _ThrowingFrappe()
    module._dimension_state = lambda dim: state
    module._dimension_members = lambda dim: set(members)
    module._driver_dimensions = lambda names: dict(
        drivers if drivers is not None else {n: "dim_zz" for n in names})
    return module


def _rs_doc(module, **over):
    doc = module.CostAllocationRuleSet.__new__(module.CostAllocationRuleSet)
    doc.entity = "ZZE"
    doc.allocation_dimension = "dim_zz"
    doc.method = "Direct"
    doc.receiver_account_mode = "Sender's Own Accounts"
    doc.allocation_account = ""
    doc.rules = [_Row(step=1, sender_member="A", driver="ZZ Head")]
    doc.receivers = [_Row(step=1, receiver_member="B"), _Row(step=1, receiver_member="C")]
    for k, v in over.items():
        setattr(doc, k, v)
    return doc


def _refusal(doc):
    try:
        doc.validate()
    except _Refused as exc:
        return str(exc)
    return None


def test_rule_set_controller_accepts_valid():
    assert _refusal(_rs_doc(_rs_module())) is None


def test_rule_set_controller_refuses_each_model_error():
    m = _rs_module()
    cases = {
        "not a member of": dict(rules=[_Row(step=1, sender_member="ZZX", driver="ZZ Head")]),
        "no receiver": dict(receivers=[]),
        "cannot receive its own": dict(receivers=[_Row(step=1, receiver_member="A")]),
        "receiver and a sender": dict(
            rules=[_Row(step=1, sender_member="A", driver="ZZ Head"),
                   _Row(step=2, sender_member="B", driver="ZZ Head")],
            receivers=[_Row(step=1, receiver_member="B"), _Row(step=2, receiver_member="C")]),
        "Allocation Account is required": dict(receiver_account_mode="Declared Allocation Account"),
        "would do nothing": dict(allocation_account="ZZ-ACC"),
        "declared more than once": dict(
            rules=[_Row(step=1, sender_member="A", driver="ZZ Head"),
                   _Row(step=1, sender_member="B", driver="ZZ Head")]),
    }
    for needle, over in cases.items():
        msg = _refusal(_rs_doc(m, **over))
        assert msg and needle in msg, (needle, msg)


def test_rule_set_controller_refuses_driver_on_other_dimension():
    m = _rs_module(drivers={"ZZ Head": "dim_other"})
    msg = _refusal(_rs_doc(m))
    assert msg and "dim_other" in msg


def test_rule_set_controller_lists_every_error_in_one_throw():
    m = _rs_module()
    doc = _rs_doc(m, receivers=[], rules=[_Row(step=1, sender_member="ZZX", driver="ZZ Head")])
    msg = _refusal(doc)
    assert "not a member of" in msg and "no receiver" in msg


def test_rule_set_controller_refuses_unpublished_dimension():
    for status in ("Draft", "Inactive", None):
        msg = _refusal(_rs_doc(_rs_module(state=(status, 1))))
        assert msg and "Published" in msg, (status, msg)


def test_rule_set_controller_refuses_dimension_not_in_budget():
    msg = _refusal(_rs_doc(_rs_module(state=("Published", 0))))
    assert msg and "In Budget" in msg


# === K11 (konsol#269): the Allocation section of the Planning sidebar ========

PLANNING_SIDEBAR = os.path.join(APP_DIR, "epm", "sidebar", "planning", "planning.json")
NAVIGATION_TEST = os.path.join(APP_DIR, "tests", "test_desk_navigation.py")
#: The Planning sidebar's modified value on feat/cost-allocation-p1, before K11.
PLANNING_MODIFIED_BEFORE_K11 = "2026-11-08 02:00:00.000000"
COST_ALLOCATION_DOCTYPES = (
    "Cost Allocation Driver", "Cost Allocation Statistic",
    "Cost Allocation Rule Set", "Cost Allocation Run")


def _planning():
    with open(PLANNING_SIDEBAR) as f:
        return json.load(f)


def test_sidebar_planning_has_allocation_section_with_four_links_in_order():
    items = _planning()["items"]
    starts = [i for i, row in enumerate(items)
              if row["type"] == "Section Break" and row["label"] == "Allocation"]
    assert len(starts) == 1, starts
    rows = items[starts[0] + 1:]
    assert all(row["type"] == "Link" and row["link_type"] == "DocType" for row in rows), rows
    assert [(row["link_to"], row["label"]) for row in rows] == [
        ("Cost Allocation Driver", "Drivers"),
        ("Cost Allocation Statistic", "Statistics"),
        ("Cost Allocation Rule Set", "Rule Sets"),
        ("Cost Allocation Run", "Runs"),
    ], rows
    assert all(row["child"] == 1 and row["is_default_module"] == 1 for row in rows), rows


def test_sidebar_planning_modified_is_later_than_before_k11():
    assert _planning()["modified"] > PLANNING_MODIFIED_BEFORE_K11, _planning()["modified"]


def test_sidebar_cost_allocation_doctypes_are_not_in_not_on_a_sidebar():
    import importlib.util
    spec = importlib.util.spec_from_file_location("_desk_navigation_k11", NAVIGATION_TEST)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    left = sorted(set(COST_ALLOCATION_DOCTYPES) & module.NOT_ON_A_SIDEBAR)
    assert left == [], left

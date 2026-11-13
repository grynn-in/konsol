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
    order = [(f["fieldtype"], f.get("fieldname") or f.get("label")) for f in _fields()]
    assert order == [
        ("Tab Break", "Driver"),
        ("Section Break", "Driver"),
        ("Data", "driver_name"),
        ("Link", "dimension"),
        ("Data", "unit"),
        ("Column Break", None),
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

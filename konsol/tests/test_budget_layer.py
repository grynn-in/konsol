"""Budget Layer doctype (L1, konsol#348 Q8-a): shape, permissions, autoname."""
import json
import os

DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "epm", "doctype", "budget_layer",
)


def _meta():
    with open(os.path.join(DIR, "budget_layer.json")) as f:
        return json.load(f)


def _fields():
    return {f["fieldname"]: f for f in _meta()["fields"]}


def test_files_exist():
    for name in ("__init__.py", "budget_layer.py", "budget_layer.json"):
        assert os.path.exists(os.path.join(DIR, name)), name


def test_identity():
    m = _meta()
    assert m["name"] == "Budget Layer"
    assert m["module"] == "EPM"
    assert m["autoname"] == "field:layer_code"
    assert m["title_field"] == "label"
    assert m["search_fields"] == "label"
    assert m["sort_field"] == "sort_order"
    assert m["sort_order"] == "ASC"
    assert m["track_changes"] == 1
    assert not m.get("is_submittable")
    assert not m.get("istable")


def test_field_shapes():
    f = _fields()
    assert f["layer_code"]["fieldtype"] == "Data"
    assert f["layer_code"]["reqd"] == 1 and f["layer_code"]["unique"] == 1
    assert f["label"]["fieldtype"] == "Data" and f["label"]["reqd"] == 1
    assert f["sort_order"]["fieldtype"] == "Int" and not f["sort_order"].get("reqd")
    assert f["writer_role"]["fieldtype"] == "Link"
    assert f["writer_role"]["options"] == "Role"
    assert not f["writer_role"].get("reqd")
    assert f["description"]["fieldtype"] == "Small Text"
    for name in ("layer_code", "writer_role", "sort_order"):
        assert f[name].get("description"), f"{name} needs help text"


def test_tab_and_sections():
    types = [f["fieldtype"] for f in _meta()["fields"]]
    assert types[0] == "Tab Break"
    assert "Section Break" in types and "Column Break" in types


def test_permissions():
    perms = {p["role"]: p for p in _meta()["permissions"]}
    admin = perms["EPM Admin"]
    for k in ("read", "write", "create", "delete"):
        assert admin.get(k) == 1, k
    for role in ("EPM Analyst", "Entity Accountant", "EPM User"):
        p = perms[role]
        assert p.get("read") == 1
        for k in ("write", "create", "delete"):
            assert not p.get(k), (role, k)


def test_controller_is_empty():
    with open(os.path.join(DIR, "budget_layer.py")) as f:
        src = f.read()
    assert "class BudgetLayer(Document)" in src
    assert "def " not in src

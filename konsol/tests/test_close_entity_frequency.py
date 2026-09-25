"""Entity reporting frequency (konsol#303 point 3, row A07).

No default: a blank reporting_frequency is a blocking gap surfaced at
sign-off (A10's completeness gate), never guessed. The field records how
often an entity is *expected* to submit a trial balance — Monthly or
Quarterly — and nothing else may read it: it must never enter Entity's
write-through to ClickHouse (CH_FIELD_MAP) or the consolidation-rebuild
watch list (_REBUILD_FIELDS), because changing it changes no consolidated
number.
"""
import ast
import json
import os

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENTITY_DIR = os.path.join(APP_DIR, "epm", "doctype", "entity")


def _src():
    with open(os.path.join(ENTITY_DIR, "entity.py")) as f:
        return f.read()


def _meta():
    with open(os.path.join(ENTITY_DIR, "entity.json")) as f:
        return json.load(f)


def _fields():
    return {f["fieldname"]: f for f in _meta()["fields"]}


def _class_attr(name):
    """A literal class attribute of Entity, read without importing frappe.
    Mirrors test_entity_registry.py's helper of the same name."""
    tree = ast.parse(_src())
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "Entity")
    for node in cls.body:
        if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == name for t in node.targets):
            return ast.literal_eval(node.value)
    raise AssertionError(f"Entity.{name} not declared")


def _assert_no_default(field):
    """The checker under test below: a field that carries a `default` has
    silently picked a policy for the customer, which A07 refuses to do."""
    if field.get("default"):
        raise AssertionError(f"{field['fieldname']} must have no default")


# ---- the field -------------------------------------------------------

def test_reporting_frequency_field_exists():
    assert "reporting_frequency" in _fields()


def test_reporting_frequency_is_a_select_with_the_three_options():
    f = _fields()["reporting_frequency"]
    assert f["fieldtype"] == "Select"
    assert f["options"] == "\nMonthly\nQuarterly"


def test_reporting_frequency_has_no_default_and_is_not_required():
    f = _fields()["reporting_frequency"]
    assert not f.get("default"), "no default: a blank frequency is a declared gap, not a guess"
    assert not f.get("reqd")
    _assert_no_default(f)  # the real checker, exercised on the real field


def test_reporting_frequency_is_placed_after_status():
    order = _meta()["field_order"]
    assert order.index("reporting_frequency") == order.index("status") + 1


# ---- must not become a consolidation input ----------------------------

def test_reporting_frequency_is_not_in_the_write_through_map():
    """Changing how often an entity reports must not touch epm_staging.entities."""
    mapped = _class_attr("CH_FIELD_MAP")
    assert "reporting_frequency" not in mapped
    assert "reporting_frequency" not in mapped.values()


def test_reporting_frequency_does_not_request_a_consolidation_rebuild():
    assert "reporting_frequency" not in _class_attr("_REBUILD_FIELDS")


# ---- failure path: the checker itself must be able to fail ------------

def test_the_no_default_checker_catches_a_default():
    try:
        _assert_no_default({"fieldname": "reporting_frequency", "default": "Monthly"})
    except AssertionError as e:
        assert "reporting_frequency must have no default" in str(e)
    else:
        raise AssertionError("_assert_no_default let a default through")

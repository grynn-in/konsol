"""TDD — Pipeline + Pipeline Step doctypes + seed fixture (PRD-12).

Pure host test (no frappe / no bench). We load the two doctype JSONs and the
seed fixture and assert:
- both doctypes expose the PRD-12 field set with the right fieldtypes;
- ``Pipeline Step.step_type`` Select options are a superset of
  :data:`konsol.orchestrator.handlers.BUILTIN_TYPES`;
- the **Group Close** seed fixture decodes to the step ids + dependency edges
  that mirror :data:`konsol.orchestrator.plan.DEFAULT_DEFINITION`.
"""
import json
import os

import pytest

from konsol.orchestrator import handlers, plan

_HERE = os.path.dirname(__file__)


def _doctype_path(name):
    return os.path.abspath(
        os.path.join(_HERE, "..", "pipeline", "doctype", name, name + ".json")
    )


_DEFN_PATH = _doctype_path("pipeline")
_STEP_PATH = _doctype_path("pipeline_step")
_FIXTURE_PATH = os.path.abspath(
    os.path.join(_HERE, "..", "fixtures", "pipeline.json")
)


def _load(path):
    with open(path) as fh:
        return json.load(fh)


def _fields_by_name(doc):
    return {f["fieldname"]: f for f in doc["fields"]}


# --- Pipeline doctype --------------------------------------------------------


def test_definition_doctype_loads_and_is_not_table():
    doc = _load(_DEFN_PATH)
    assert doc["name"] == "Pipeline"
    assert doc["module"] == "Pipeline"
    assert doc.get("istable", 0) == 0


def test_definition_autoname_field():
    doc = _load(_DEFN_PATH)
    assert doc.get("autoname") == "field:pipeline_name"


def test_definition_fields_present():
    fields = _fields_by_name(_load(_DEFN_PATH))
    for name in (
        "pipeline_name",
        "title",
        "description",
        "enabled",
        "default_params",
        "steps",
    ):
        assert name in fields, f"missing Pipeline field {name!r}"


def test_definition_name_unique():
    fields = _fields_by_name(_load(_DEFN_PATH))
    assert fields["pipeline_name"]["fieldtype"] == "Data"
    assert fields["pipeline_name"].get("unique") == 1


def test_definition_enabled_default_on():
    fields = _fields_by_name(_load(_DEFN_PATH))
    assert fields["enabled"]["fieldtype"] == "Check"
    assert str(fields["enabled"].get("default")) == "1"


def test_definition_steps_is_table_to_pipeline_step():
    fields = _fields_by_name(_load(_DEFN_PATH))
    assert fields["steps"]["fieldtype"] == "Table"
    assert fields["steps"]["options"] == "Pipeline Step"


def test_definition_default_params_is_json_code():
    fields = _fields_by_name(_load(_DEFN_PATH))
    assert fields["default_params"]["fieldtype"] in {"Code", "JSON", "Small Text"}


# --- Pipeline Step doctype ---------------------------------------------------


def test_pipeline_step_doctype_is_child_table():
    doc = _load(_STEP_PATH)
    assert doc["name"] == "Pipeline Step"
    assert doc["module"] == "Pipeline"
    assert doc["istable"] == 1


def test_pipeline_step_fields_present():
    fields = _fields_by_name(_load(_STEP_PATH))
    for name in ("step_id", "step_type", "depends_on", "params"):
        assert name in fields, f"missing Pipeline Step field {name!r}"


def test_step_id_required():
    fields = _fields_by_name(_load(_STEP_PATH))
    assert fields["step_id"]["fieldtype"] == "Data"
    assert fields["step_id"].get("reqd") == 1


def test_step_type_is_select_with_builtin_options():
    fields = _fields_by_name(_load(_STEP_PATH))
    assert fields["step_type"]["fieldtype"] == "Select"
    options = {o.strip() for o in fields["step_type"]["options"].split("\n") if o.strip()}
    assert set(handlers.BUILTIN_TYPES).issubset(options), (
        f"step_type options {options} must cover BUILTIN_TYPES {handlers.BUILTIN_TYPES}"
    )


def test_step_type_options_include_extra_types():
    fields = _fields_by_name(_load(_STEP_PATH))
    options = {o.strip() for o in fields["step_type"]["options"].split("\n") if o.strip()}
    for extra in ("cube_refresh", "sql"):
        assert extra in options, f"expected extra step_type {extra!r}"


def test_pipeline_step_depends_on_and_params_types():
    fields = _fields_by_name(_load(_STEP_PATH))
    assert fields["depends_on"]["fieldtype"] in {"Small Text", "Data", "Long Text"}
    assert fields["params"]["fieldtype"] in {"Code", "JSON", "Small Text"}


# --- Group Close seed fixture -----------------------------------------------


def _group_close():
    records = _load(_FIXTURE_PATH)
    assert isinstance(records, list) and records, "fixture must be a non-empty list"
    for rec in records:
        if rec.get("pipeline_name") == "Group Close":
            return rec
    raise AssertionError("Group Close definition not found in fixture")


def test_fixture_is_pipeline():
    rec = _group_close()
    assert rec["doctype"] == "Pipeline"
    assert rec.get("enabled", 1)


def test_fixture_step_ids_mirror_default_definition():
    rec = _group_close()
    fixture_ids = [s["step_id"] for s in rec["steps"]]
    expected_ids = [s.id for s in plan.DEFAULT_DEFINITION]
    assert fixture_ids == expected_ids


def test_fixture_step_types_mirror_default_definition():
    rec = _group_close()
    by_id = {s["step_id"]: s for s in rec["steps"]}
    for tmpl in plan.DEFAULT_DEFINITION:
        assert by_id[tmpl.id]["step_type"] == tmpl.type


def test_fixture_dependency_edges_mirror_default_definition():
    rec = _group_close()
    by_id = {s["step_id"]: s for s in rec["steps"]}
    for tmpl in plan.DEFAULT_DEFINITION:
        raw = by_id[tmpl.id].get("depends_on") or ""
        deps = [d.strip() for d in raw.split(",") if d.strip()]
        assert deps == list(tmpl.depends_on), f"deps mismatch for {tmpl.id!r}"


def test_fixture_step_types_are_valid_builtins():
    rec = _group_close()
    for s in rec["steps"]:
        assert s["step_type"] in handlers.BUILTIN_TYPES


def test_fixture_registered_in_hooks():
    hooks_path = os.path.abspath(os.path.join(_HERE, "..", "hooks.py"))
    with open(hooks_path) as fh:
        text = fh.read()
    assert '"Pipeline",' in text, "register the fixture in hooks.py fixtures"

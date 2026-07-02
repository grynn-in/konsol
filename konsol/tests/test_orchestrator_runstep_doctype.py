"""TDD — Run Step doctype fields (PRD-6).

Doctype JSON PRD: no frappe needed. We load the `run_step` child
doctype JSON and assert the orchestrator persistence fields exist with
the expected fieldtypes, while leaving the pre-existing fields intact.
"""
import json
import os

import pytest

_HERE = os.path.dirname(__file__)
_JSON_PATH = os.path.abspath(
    os.path.join(
        _HERE,
        "..",
        "pipeline",
        "doctype",
        "run_step",
        "run_step.json",
    )
)


def _load():
    with open(_JSON_PATH) as fh:
        return json.load(fh)


def _fields_by_name(doc):
    return {f["fieldname"]: f for f in doc["fields"]}


def test_json_loads_and_is_child_table():
    doc = _load()
    assert doc["name"] == "Run Step"
    assert doc["istable"] == 1


def test_existing_fields_preserved():
    fields = _fields_by_name(_load())
    for name in ("stage", "step", "status", "rows", "duration", "output"):
        assert name in fields, f"existing field {name!r} must be preserved"


def test_orchestrator_fields_present():
    fields = _fields_by_name(_load())
    for name in (
        "step_id",
        "step_type",
        "depends_on",
        "params",
        "retry_count",
        "started_at",
        "ended_at",
        "error",
    ):
        assert name in fields, f"missing orchestrator field {name!r}"


@pytest.mark.parametrize(
    "name,expected_types",
    [
        ("step_id", {"Data"}),
        ("step_type", {"Data", "Select"}),
        ("depends_on", {"Small Text", "Long Text", "Code"}),
        ("params", {"Code", "JSON"}),
        ("retry_count", {"Int"}),
        ("started_at", {"Datetime"}),
        ("ended_at", {"Datetime"}),
        ("error", {"Small Text", "Long Text", "Code", "Text"}),
    ],
)
def test_orchestrator_field_types(name, expected_types):
    fields = _fields_by_name(_load())
    assert fields[name]["fieldtype"] in expected_types


def test_every_field_has_label():
    for f in _load()["fields"]:
        assert f.get("label"), f"field {f['fieldname']!r} needs a label"

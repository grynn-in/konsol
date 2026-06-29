"""TDD — Pipeline Run params (PRD-7).

Doctype JSON PRD: no frappe needed. We load the `pipeline_run` doctype
JSON and assert the run-level orchestrator parameter fields exist with
sensible fieldtypes, while leaving the pre-existing fields intact. These
fields mirror the ``params`` keys consumed by ``plan.build_plan`` —
``skip_sync``, ``full_refresh``, ``scope``→select, ``fiscal_year`` /
``fiscal_period``→vars, plus ``pipeline_definition``.
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
        "pipeline_run",
        "pipeline_run.json",
    )
)


def _load():
    with open(_JSON_PATH) as fh:
        return json.load(fh)


def _fields_by_name(doc):
    return {f["fieldname"]: f for f in doc["fields"]}


def test_json_loads_and_is_doctype():
    doc = _load()
    assert doc["name"] == "Pipeline Run"
    assert doc["istable"] == 0


def test_existing_fields_preserved():
    fields = _fields_by_name(_load())
    for name in (
        "status",
        "progress_pct",
        "triggered_by",
        "started_at",
        "completed_at",
        "steps",
        "log",
        "error_log",
    ):
        assert name in fields, f"existing field {name!r} must be preserved"


def test_param_fields_present():
    fields = _fields_by_name(_load())
    for name in (
        "fiscal_year",
        "fiscal_period",
        "scope",
        "full_refresh",
        "skip_sync",
        "pipeline_definition",
    ):
        assert name in fields, f"missing param field {name!r}"


@pytest.mark.parametrize(
    "name,expected_types",
    [
        ("fiscal_year", {"Int", "Data"}),
        ("fiscal_period", {"Int", "Data"}),
        ("scope", {"Data", "Small Text"}),
        ("full_refresh", {"Check"}),
        ("skip_sync", {"Check"}),
        ("pipeline_definition", {"Data", "Select", "Link"}),
    ],
)
def test_param_field_types(name, expected_types):
    fields = _fields_by_name(_load())
    assert fields[name]["fieldtype"] in expected_types


def test_param_fields_have_labels():
    fields = _fields_by_name(_load())
    for name in (
        "fiscal_year",
        "fiscal_period",
        "scope",
        "full_refresh",
        "skip_sync",
        "pipeline_definition",
    ):
        assert fields[name].get("label"), f"param field {name!r} needs a label"

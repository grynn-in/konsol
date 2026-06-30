"""TDD — orchestrator Definition->plan loader (PRD-13).

``konsol.orchestrator.definition`` turns PRD-12 Pipeline Definition doctype data
(a dict, e.g. ``Pipeline Definition.as_dict()`` or the seed fixture record) into
``dag.Step`` objects so ``plan.build_plan`` can consume user-authored definitions
instead of the hardcoded ``DEFAULT_DEFINITION`` constant.

The pure converter ``definition_to_steps`` imports + runs on the host without a
bench; the frappe-bound ``load_definition`` is guarded with importorskip.
"""
import json
import os

import pytest

from konsol.orchestrator import definition
from konsol.orchestrator.dag import Dag, Step
from konsol.orchestrator.plan import DEFAULT_DEFINITION, build_plan


FIXTURE = os.path.join(
    os.path.dirname(__file__),
    "..",
    "fixtures",
    "pipeline_definition.json",
)


def _group_close_record():
    with open(FIXTURE) as fh:
        return json.load(fh)[0]


# ---- module hygiene ------------------------------------------------------

def test_module_imports_without_frappe():
    # frappe is not installed on host; importing the module must not need it.
    assert hasattr(definition, "definition_to_steps")
    assert hasattr(definition, "load_definition")


def test_no_toplevel_frappe_import():
    src = open(definition.__file__).read()
    # all frappe imports must be function-local
    for line in src.splitlines():
        stripped = line.strip()
        if stripped.startswith("import frappe") or stripped.startswith("from frappe"):
            assert line.startswith(" ") or line.startswith("\t"), (
                f"top-level frappe import found: {line!r}"
            )


# ---- definition_to_steps -------------------------------------------------

def test_returns_step_objects_in_order():
    steps = definition.definition_to_steps(_group_close_record())
    assert all(isinstance(s, Step) for s in steps)
    assert [s.id for s in steps] == [
        "extract",
        "seed",
        "silver",
        "gold",
        "assertions",
        "signoff",
    ]


def test_types_match_fixture():
    steps = definition.definition_to_steps(_group_close_record())
    assert [s.type for s in steps] == [
        "airbyte_sync",
        "dbt_seed",
        "dbt_run",
        "dbt_run",
        "close_assertions",
        "signoff",
    ]


def test_depends_on_parsed_empty_to_list():
    steps = {s.id: s for s in definition.definition_to_steps(_group_close_record())}
    assert steps["extract"].depends_on == []
    assert steps["seed"].depends_on == ["extract"]
    assert steps["silver"].depends_on == ["seed"]
    assert steps["signoff"].depends_on == ["assertions"]


def test_depends_on_comma_split_and_strip():
    defn = {
        "steps": [
            {"step_id": "a", "step_type": "dbt_run", "depends_on": "", "params": ""},
            {"step_id": "b", "step_type": "dbt_run", "depends_on": "", "params": ""},
            {
                "step_id": "c",
                "step_type": "dbt_run",
                "depends_on": " a , b ",
                "params": "",
            },
        ]
    }
    steps = {s.id: s for s in definition.definition_to_steps(defn)}
    assert steps["c"].depends_on == ["a", "b"]


def test_params_json_str_parsed():
    defn = {
        "steps": [
            {
                "step_id": "x",
                "step_type": "dbt_run",
                "depends_on": "",
                "params": '{"select": "tag:silver", "full_refresh": true}',
            }
        ]
    }
    steps = definition.definition_to_steps(defn)
    assert steps[0].params == {"select": "tag:silver", "full_refresh": True}


def test_params_empty_str_to_empty_dict():
    steps = definition.definition_to_steps(_group_close_record())
    assert all(s.params == {} for s in steps)


def test_params_dict_passthrough():
    defn = {
        "steps": [
            {
                "step_id": "x",
                "step_type": "dbt_run",
                "depends_on": "",
                "params": {"select": "tag:gold"},
            }
        ]
    }
    steps = definition.definition_to_steps(defn)
    assert steps[0].params == {"select": "tag:gold"}


def test_bad_json_params_raises_clear_error():
    defn = {
        "steps": [
            {
                "step_id": "x",
                "step_type": "dbt_run",
                "depends_on": "",
                "params": "{not valid json",
            }
        ]
    }
    with pytest.raises(ValueError) as exc:
        definition.definition_to_steps(defn)
    assert "x" in str(exc.value)


def test_missing_steps_key_yields_empty():
    assert definition.definition_to_steps({}) == []


# ---- round-trips ---------------------------------------------------------

def test_roundtrip_toposort():
    steps = definition.definition_to_steps(_group_close_record())
    order = [s.id for s in Dag(steps).toposort()]
    assert order == ["extract", "seed", "silver", "gold", "assertions", "signoff"]


def test_roundtrip_build_plan_matches_default_definition():
    steps = definition.definition_to_steps(_group_close_record())
    loaded = build_plan(steps, {})
    canonical = build_plan(DEFAULT_DEFINITION, {})
    assert [(s.id, s.type, s.depends_on) for s in loaded] == [
        (s.id, s.type, s.depends_on) for s in canonical
    ]


def test_roundtrip_build_plan_skip_sync():
    steps = definition.definition_to_steps(_group_close_record())
    loaded = build_plan(steps, {"skip_sync": True})
    assert [s.id for s in loaded] == ["seed", "silver", "gold", "assertions", "signoff"]
    # seed's airbyte dependency rewired away
    assert {s.id: s.depends_on for s in loaded}["seed"] == []


# ---- runtime wiring (#65a) -----------------------------------------------

def test_run_pipeline_loads_definition_when_set():
    # The frappe-bound entrypoint must drive the run's pipeline_definition through
    # the planner for runs that carry one. Since #65 B1 the str->Steps resolution
    # lives in ``plan_run`` (the single resolution point) via load_definition;
    # run_pipeline just passes the definition name through. (site-free source
    # check; the actual frappe.get_doc is exercised in a bench smoke test.)
    import inspect

    from konsol.orchestrator import run

    rp_src = inspect.getsource(run.run_pipeline)
    assert "pipeline_definition" in rp_src
    assert "plan_run" in rp_src
    # resolution moved into plan_run
    pr_src = inspect.getsource(run.plan_run)
    assert "load_definition" in pr_src


def test_loaded_definition_drives_plan_run():
    # End-to-end (pure): a loaded definition flows through plan_run to the Dag.
    from konsol.orchestrator import run

    steps = definition.definition_to_steps(_group_close_record())
    dag, _ = run.plan_run({}, definition=steps)
    assert [s.id for s in dag.steps] == [
        "extract",
        "seed",
        "silver",
        "gold",
        "assertions",
        "signoff",
    ]


# ---- frappe-bound loader (guarded) ---------------------------------------

def test_load_definition_requires_frappe():
    frappe = pytest.importorskip("frappe")
    assert callable(definition.load_definition)

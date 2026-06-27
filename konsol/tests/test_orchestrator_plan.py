"""TDD — orchestrator run-plan resolution (PRD-2). Pure-python."""
from konsol.orchestrator.dag import Dag
from konsol.orchestrator.plan import DEFAULT_DEFINITION, build_plan


def _by_id(steps):
    return {s.id: s for s in steps}


def test_default_plan_is_a_valid_ordered_dag():
    plan = build_plan(DEFAULT_DEFINITION, {})
    order = [s.id for s in Dag(plan).toposort()]
    assert order[0] == "extract"
    assert order[-1] == "signoff"
    pos = {sid: i for i, sid in enumerate(order)}
    assert pos["extract"] < pos["seed"] < pos["silver"] < pos["gold"]
    assert pos["gold"] < pos["assertions"] < pos["signoff"]


def test_skip_sync_drops_airbyte_step_and_rewires_deps():
    plan = build_plan(DEFAULT_DEFINITION, {"skip_sync": True})
    ids = {s.id for s in plan}
    assert "extract" not in ids
    # no remaining step may reference the dropped extract step
    for s in plan:
        assert "extract" not in s.depends_on
    # still a valid DAG
    Dag(plan).toposort()


def test_full_refresh_sets_flag_on_dbt_run_build_only():
    plan = _by_id(build_plan(DEFAULT_DEFINITION, {"full_refresh": True}))
    assert plan["silver"].params.get("full_refresh") is True
    assert plan["gold"].params.get("full_refresh") is True
    # seed and non-dbt steps are untouched
    assert plan["seed"].params.get("full_refresh") is not True
    assert plan["signoff"].params.get("full_refresh") is not True


def test_scope_maps_to_select_on_transform_steps():
    plan = _by_id(build_plan(DEFAULT_DEFINITION, {"scope": "consolidation"}))
    assert plan["silver"].params.get("select") == "consolidation"
    assert plan["gold"].params.get("select") == "consolidation"
    assert plan["seed"].params.get("select") is None


def test_fiscal_params_become_dbt_vars():
    plan = _by_id(build_plan(DEFAULT_DEFINITION, {"fiscal_year": 2024, "fiscal_period": 12}))
    assert plan["gold"].params["vars"]["fiscal_year"] == 2024
    assert plan["gold"].params["vars"]["fiscal_period"] == 12
    assert plan["assertions"].params["vars"]["fiscal_year"] == 2024


def test_build_plan_does_not_mutate_definition():
    before = [dict(s.params) for s in DEFAULT_DEFINITION]
    build_plan(DEFAULT_DEFINITION, {"full_refresh": True, "scope": "full"})
    after = [dict(s.params) for s in DEFAULT_DEFINITION]
    assert before == after

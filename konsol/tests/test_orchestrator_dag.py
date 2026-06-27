"""TDD — orchestrator DAG core (PRD-1). Pure-python; runs on host pytest."""
import pytest

from konsol.orchestrator.dag import Step, Dag, DagError


def _steps():
    return [
        Step(id="extract", type="airbyte_sync"),
        Step(id="seed", type="dbt_seed", depends_on=["extract"]),
        Step(id="silver", type="dbt_run", depends_on=["seed"]),
        Step(id="gold", type="dbt_run", depends_on=["silver"]),
        Step(id="assert", type="close_assertions", depends_on=["gold"]),
    ]


def test_step_defaults():
    s = Step(id="x", type="dbt_run")
    assert s.depends_on == []
    assert s.params == {}


def test_toposort_respects_dependencies():
    dag = Dag(_steps())
    order = [s.id for s in dag.toposort()]
    # every dependency must precede its dependent
    pos = {sid: i for i, sid in enumerate(order)}
    assert pos["extract"] < pos["seed"] < pos["silver"] < pos["gold"] < pos["assert"]


def test_toposort_handles_diamond():
    steps = [
        Step(id="a", type="t"),
        Step(id="b", type="t", depends_on=["a"]),
        Step(id="c", type="t", depends_on=["a"]),
        Step(id="d", type="t", depends_on=["b", "c"]),
    ]
    order = [s.id for s in Dag(steps).toposort()]
    pos = {sid: i for i, sid in enumerate(order)}
    assert pos["a"] < pos["b"] < pos["d"]
    assert pos["a"] < pos["c"] < pos["d"]


def test_cycle_is_rejected():
    steps = [
        Step(id="a", type="t", depends_on=["b"]),
        Step(id="b", type="t", depends_on=["a"]),
    ]
    with pytest.raises(DagError, match="cycle"):
        Dag(steps).toposort()


def test_unknown_dependency_rejected():
    steps = [Step(id="a", type="t", depends_on=["ghost"])]
    with pytest.raises(DagError, match="unknown"):
        Dag(steps)


def test_duplicate_id_rejected():
    steps = [Step(id="a", type="t"), Step(id="a", type="t")]
    with pytest.raises(DagError, match="duplicate"):
        Dag(steps)


def test_get_and_dependents():
    dag = Dag(_steps())
    assert dag.get("silver").type == "dbt_run"
    # descendants of seed = everything downstream
    assert dag.descendants("seed") == {"silver", "gold", "assert"}


def test_roots():
    dag = Dag(_steps())
    assert [s.id for s in dag.roots()] == ["extract"]

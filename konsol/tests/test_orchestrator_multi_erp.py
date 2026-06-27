"""TDD — orchestrator multi-ERP extract (PRD-19). Pure-python, no frappe.

A definition may carry multiple ``airbyte_sync`` (extract) steps, each tagged
with a ``params.source``. A run's ``sources`` param keeps only the matching
extracts (dropping + rewiring the rest, like ``skip_sync``); all retained
extracts fan into ``seed``. ``skip_sync`` still drops *all* extracts and wins
over ``sources``.
"""
from konsol.orchestrator.dag import Dag, Step
from konsol.orchestrator.plan import build_plan, merge_sources


def _two_extract_definition():
    """seed fans in from two source-tagged extracts; then the usual chain."""
    return [
        Step(id="extract_d365", type="airbyte_sync", params={"source": "d365"}),
        Step(id="extract_erpnext", type="airbyte_sync", params={"source": "erpnext"}),
        Step(id="seed", type="dbt_seed", depends_on=["extract_d365", "extract_erpnext"]),
        Step(id="silver", type="dbt_run", depends_on=["seed"]),
        Step(id="gold", type="dbt_run", depends_on=["silver"]),
    ]


def _by_id(steps):
    return {s.id: s for s in steps}


# ----- merge_sources helper -----------------------------------------------

def test_merge_sources_keeps_only_selected_extract():
    merged = merge_sources(_two_extract_definition(), ["d365"])
    ids = {s.id for s in merged}
    assert "extract_d365" in ids
    assert "extract_erpnext" not in ids
    # the dropped extract is stripped from seed's depends_on
    seed = _by_id(merged)["seed"]
    assert "extract_erpnext" not in seed.depends_on
    assert "extract_d365" in seed.depends_on


def test_merge_sources_none_keeps_all_extracts():
    merged = merge_sources(_two_extract_definition(), None)
    ids = {s.id for s in merged}
    assert "extract_d365" in ids
    assert "extract_erpnext" in ids


def test_merge_sources_does_not_mutate_definition():
    defn = _two_extract_definition()
    before = [s.depends_on[:] for s in defn]
    merge_sources(defn, ["d365"])
    after = [s.depends_on[:] for s in defn]
    assert before == after


def test_merge_sources_multiple_selected():
    merged = merge_sources(_two_extract_definition(), ["d365", "erpnext"])
    ids = {s.id for s in merged}
    assert "extract_d365" in ids
    assert "extract_erpnext" in ids


# ----- build_plan integration ---------------------------------------------

def test_sources_keeps_one_extract_and_yields_valid_dag():
    plan = build_plan(_two_extract_definition(), {"sources": ["d365"]})
    ids = {s.id for s in plan}
    assert "extract_d365" in ids
    assert "extract_erpnext" not in ids
    order = [s.id for s in Dag(plan).toposort()]  # valid acyclic DAG
    # seed still reachable and after the retained extract
    pos = {sid: i for i, sid in enumerate(order)}
    assert pos["extract_d365"] < pos["seed"] < pos["silver"] < pos["gold"]


def test_no_sources_keeps_both_extracts():
    plan = build_plan(_two_extract_definition(), {})
    ids = {s.id for s in plan}
    assert "extract_d365" in ids
    assert "extract_erpnext" in ids
    seed = _by_id(plan)["seed"]
    assert set(seed.depends_on) == {"extract_d365", "extract_erpnext"}
    Dag(plan).toposort()


def test_retained_extracts_fan_into_seed():
    plan = build_plan(_two_extract_definition(), {"sources": ["erpnext"]})
    seed = _by_id(plan)["seed"]
    assert seed.depends_on == ["extract_erpnext"]
    Dag(plan).toposort()


def test_skip_sync_drops_all_extracts_and_wins_over_sources():
    plan = build_plan(_two_extract_definition(), {"skip_sync": True, "sources": ["d365"]})
    ids = {s.id for s in plan}
    assert "extract_d365" not in ids
    assert "extract_erpnext" not in ids
    seed = _by_id(plan)["seed"]
    assert seed.depends_on == []  # rewired to a root
    Dag(plan).toposort()


def test_skip_sync_alone_drops_all_extracts():
    plan = build_plan(_two_extract_definition(), {"skip_sync": True})
    ids = {s.id for s in plan}
    assert "extract_d365" not in ids
    assert "extract_erpnext" not in ids
    Dag(plan).toposort()


def test_sources_empty_list_drops_all_extracts():
    plan = build_plan(_two_extract_definition(), {"sources": []})
    ids = {s.id for s in plan}
    assert "extract_d365" not in ids
    assert "extract_erpnext" not in ids
    seed = _by_id(plan)["seed"]
    assert seed.depends_on == []
    Dag(plan).toposort()

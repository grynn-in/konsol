"""PRD-16 — per-step metrics + lineage (pure core).

Host tests for the pure :mod:`konsol.orchestrator.lineage` module: the static
``STEP_OUTPUTS`` table map, ``lineage_for`` (DAG edges + step->table edges) and
``summarize`` (rollup of the metrics the Frappe sink records on each child row).
No frappe at top level.
"""
import os

from konsol.orchestrator import lineage
from konsol.orchestrator.plan import DEFAULT_DEFINITION
from konsol.orchestrator.state import Status


ORCH_DIR = os.path.dirname(lineage.__file__)


# ---- purity / no top-level frappe ------------------------------------------

def test_lineage_module_has_no_toplevel_frappe_import():
    with open(os.path.join(ORCH_DIR, "lineage.py")) as fh:
        src = fh.read()
    for line in src.splitlines():
        if line.startswith("import frappe") or line.startswith("from frappe"):
            raise AssertionError("lineage.py must not import frappe at top level")


# ---- STEP_OUTPUTS ----------------------------------------------------------

def test_step_outputs_maps_known_steps_to_tables():
    assert lineage.STEP_OUTPUTS["silver"] == ["silver_*"]
    assert lineage.STEP_OUTPUTS["gold"] == ["gold_*"]
    assert lineage.STEP_OUTPUTS["assertions"] == ["close_assertions"]


def test_step_outputs_signoff_produces_nothing():
    assert lineage.STEP_OUTPUTS["signoff"] == []


# ---- lineage_for -----------------------------------------------------------

def test_lineage_for_has_the_five_dependency_edges():
    edges = lineage.lineage_for(DEFAULT_DEFINITION)
    for edge in [
        ("extract", "seed"),
        ("seed", "silver"),
        ("silver", "gold"),
        ("gold", "assertions"),
        ("assertions", "signoff"),
    ]:
        assert edge in edges


def test_lineage_for_has_step_to_table_edges():
    edges = lineage.lineage_for(DEFAULT_DEFINITION)
    assert ("silver", "silver_*") in edges
    assert ("gold", "gold_*") in edges
    assert ("assertions", "close_assertions") in edges


def test_lineage_for_signoff_has_no_table_edge():
    edges = lineage.lineage_for(DEFAULT_DEFINITION)
    assert not any(up == "signoff" and down != "signoff" and down not in
                   {s.id for s in DEFAULT_DEFINITION} for up, down in edges)
    # signoff produces no table, so no (signoff, <table>) edge exists
    assert all(not (up == "signoff" and "_" in down) for up, down in edges)


# ---- summarize -------------------------------------------------------------

def _snap(**kw):
    return {s.id: kw.get(s.id, Status.SUCCESS) for s in DEFAULT_DEFINITION}


def test_summarize_totals_sum_rows_and_duration():
    metrics = {
        "extract": {"rows": 100, "duration_s": 2},
        "seed": {"rows": 10, "duration_s": 1},
        "silver": {"rows": 500, "duration_s": 5},
    }
    out = lineage.summarize(_snap(), metrics)
    assert out["total_rows"] == 610
    assert out["duration_s"] == 8


def test_summarize_per_step_shaping():
    metrics = {"silver": {"rows": 500, "duration_s": 5}}
    out = lineage.summarize(_snap(), metrics)
    assert [p["step_id"] for p in out["per_step"]] == [s.id for s in DEFAULT_DEFINITION]
    silver = next(p for p in out["per_step"] if p["step_id"] == "silver")
    assert silver == {"step_id": "silver", "status": Status.SUCCESS,
                      "rows": 500, "duration_s": 5}


def test_summarize_missing_metrics_default_to_zero():
    out = lineage.summarize(_snap(), {})
    assert out["total_rows"] == 0
    assert out["duration_s"] == 0
    assert all(p["rows"] == 0 and p["duration_s"] == 0 for p in out["per_step"])


def test_summarize_rollup_success_when_all_settled_success():
    out = lineage.summarize(_snap(), {})
    assert out["status"] == Status.SUCCESS


def test_summarize_rollup_failed_when_any_failed():
    out = lineage.summarize(_snap(gold=Status.FAILED), {})
    assert out["status"] == Status.FAILED


def test_summarize_rollup_running_when_any_running():
    out = lineage.summarize(_snap(gold=Status.RUNNING), {})
    assert out["status"] == Status.RUNNING


def test_summarize_skipped_counts_as_success():
    out = lineage.summarize(_snap(extract=Status.SKIPPED), {})
    assert out["status"] == Status.SUCCESS


def test_summarize_empty_run_shaping():
    out = lineage.summarize({}, {})
    assert out["total_rows"] == 0
    assert out["duration_s"] == 0
    assert out["per_step"] == []
    assert out["status"] == Status.PENDING


def test_summarize_tolerates_none_metrics():
    out = lineage.summarize(_snap(), None)
    assert out["total_rows"] == 0

"""TDD — orchestrator Frappe executor binding (PRD-9).

The binding module ``konsol.orchestrator.run`` ties the pure core to a Pipeline
Run doc. It must import on the host **without** frappe (all frappe imports live
inside functions); the pure parts (param mapping, plan/state construction, the
progress sink) are unit-tested here with plain fakes — no bench required.
"""
import json

from konsol.orchestrator import run
from konsol.orchestrator.dag import Dag, Step
from konsol.orchestrator.executor import Executor, StepContext
from konsol.orchestrator.handlers import StepResult
from konsol.orchestrator.state import RunState, Status
from konsol.orchestrator import handlers


# ---- fakes ---------------------------------------------------------------

class FakeRow:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class FakeDoc:
    """Stand-in for a Pipeline Run doc with a ``steps`` child table."""

    def __init__(self, **fields):
        self.__dict__.update(fields)
        if not hasattr(self, "steps"):
            self.steps = []
        self.saved = False

    def append(self, table, values):
        row = FakeRow(**values)
        getattr(self, table).append(row)
        return row

    def save(self, *a, **k):
        self.saved = True


# ---- module import / surface --------------------------------------------

def test_run_module_imports_without_frappe():
    # importing the module must not require frappe; run_pipeline is callable
    assert callable(run.run_pipeline)
    assert callable(run.params_from_doc)
    assert callable(run.plan_run)
    assert hasattr(run, "FrappeSink")


# ---- params_from_doc (pure) ---------------------------------------------

def test_params_from_doc_maps_prd7_fields():
    doc = FakeDoc(
        fiscal_year=2024,
        fiscal_period=12,
        scope="domain:consolidation",
        full_refresh=1,
        skip_sync=0,
        pipeline_definition="Group Close",
    )
    p = run.params_from_doc(doc)
    assert p["fiscal_year"] == 2024
    assert p["fiscal_period"] == 12
    assert p["scope"] == "domain:consolidation"
    assert p["full_refresh"] is True
    assert p["skip_sync"] is False
    assert p["pipeline_definition"] == "Group Close"


def test_params_from_doc_checks_become_bools():
    doc = FakeDoc(full_refresh=1, skip_sync=1)
    p = run.params_from_doc(doc)
    assert p["full_refresh"] is True
    assert p["skip_sync"] is True


def test_params_from_doc_zero_fiscal_is_none():
    # Frappe Int fields default to 0 — treat 0/empty as "not set"
    doc = FakeDoc(fiscal_year=0, fiscal_period=0, scope="")
    p = run.params_from_doc(doc)
    assert p["fiscal_year"] is None
    assert p["fiscal_period"] is None
    assert p["scope"] is None


def test_params_from_doc_accepts_dict():
    p = run.params_from_doc({"fiscal_year": 2025, "skip_sync": 1})
    assert p["fiscal_year"] == 2025
    assert p["skip_sync"] is True
    assert p["full_refresh"] is False


# ---- plan_run (pure) -----------------------------------------------------

def test_plan_run_default_definition():
    dag, state = run.plan_run({})
    ids = [s.id for s in dag.steps]
    assert ids == ["extract", "seed", "silver", "gold", "assertions", "signoff"]
    assert isinstance(state, RunState)
    assert all(state.status(i) == Status.PENDING for i in ids)


def test_plan_run_skip_sync_drops_extract():
    dag, state = run.plan_run({"skip_sync": True})
    ids = [s.id for s in dag.steps]
    assert "extract" not in ids
    # seed is now a root (its airbyte dep was rewired away)
    assert state.runnable()[0].id == "seed"


def test_plan_run_full_refresh_flag_on_dbt():
    dag, _ = run.plan_run({"full_refresh": True})
    silver = dag.get("silver")
    assert silver.params.get("full_refresh") is True


def test_plan_run_honors_passed_definition():
    # #65a: plan_run plans from a supplied definition over DEFAULT_DEFINITION.
    dag, state = run.plan_run({}, definition=[Step("only", "signoff")])
    assert [s.id for s in dag.steps] == ["only"]
    assert all(state.status(s.id) == Status.PENDING for s in dag.steps)


def test_plan_run_defaults_to_default_definition_when_none():
    from konsol.orchestrator.plan import DEFAULT_DEFINITION

    dag, _ = run.plan_run({}, definition=None)
    assert [s.id for s in dag.steps] == [s.id for s in DEFAULT_DEFINITION]


# ---- run metadata helpers (#65b, pure) ----------------------------------

def test_progress_pct_is_100_on_full_success():
    dag, state = run.plan_run({"skip_sync": True})
    for s in dag.steps:
        state.mark(s.id, Status.SUCCESS)
    assert run.progress_pct(state) == 100


def test_progress_pct_partial_counts_success_and_skipped():
    dag = Dag([Step("a", "t"), Step("b", "t"), Step("c", "t"), Step("d", "t")])
    state = RunState(dag)
    state.mark("a", Status.SUCCESS)
    state.mark("b", Status.SKIPPED)  # both count as satisfied
    state.mark("c", Status.FAILED)
    assert run.progress_pct(state) == 50


def test_rows_synced_sums_only_airbyte_steps():
    doc = FakeDoc()
    doc.steps = [
        FakeRow(step_type="airbyte_sync", rows=100),
        FakeRow(step_type="dbt_run", rows=5),
        FakeRow(step_type="airbyte_sync", rows=23),
    ]
    assert run.rows_synced_from_doc(doc) == 123


def test_rows_synced_zero_when_no_extract():
    doc = FakeDoc()
    doc.steps = [FakeRow(step_type="dbt_run", rows=5)]
    assert run.rows_synced_from_doc(doc) == 0


# ---- FrappeSink (pure, with fake doc) -----------------------------------

def test_sink_on_step_start_creates_child_row():
    doc = FakeDoc()
    sink = run.FrappeSink(doc, now=lambda: "T0")
    sink.on_step_start(Step("gold", "dbt_run"))
    assert len(doc.steps) == 1
    row = doc.steps[0]
    assert row.step_id == "gold"
    assert row.step_type == "dbt_run"
    assert row.status == Status.RUNNING
    assert row.started_at == "T0"


def test_sink_on_step_result_updates_same_row():
    doc = FakeDoc()
    sink = run.FrappeSink(doc, now=lambda: "T1")
    step = Step("gold", "dbt_run")
    sink.on_step_start(step)
    sink.on_step_result(step, StepResult(ok=True, rows=42, log="ran gold"))
    # no duplicate row
    assert len(doc.steps) == 1
    row = doc.steps[0]
    assert row.status == Status.SUCCESS
    assert row.ended_at == "T1"
    assert row.rows == 42
    assert row.output == "ran gold"
    assert row.error == ""


def test_sink_failure_records_error_and_failed_status():
    doc = FakeDoc()
    sink = run.FrappeSink(doc)
    step = Step("seed", "dbt_seed")
    sink.on_step_start(step)
    sink.on_step_result(step, StepResult(ok=False, error="boom"))
    row = doc.steps[0]
    assert row.status == Status.FAILED
    assert row.error == "boom"


def test_sink_result_without_prior_start_creates_row():
    doc = FakeDoc()
    sink = run.FrappeSink(doc)
    sink.on_step_result(Step("x", "signoff"), StepResult(ok=True))
    assert len(doc.steps) == 1
    assert doc.steps[0].step_id == "x"
    assert doc.steps[0].status == Status.SUCCESS


def test_sink_calls_publish_callback():
    doc = FakeDoc()
    events = []
    sink = run.FrappeSink(doc, publish=lambda event, payload: events.append((event, payload)))
    step = Step("gold", "dbt_run")
    sink.on_step_start(step)
    sink.on_step_result(step, StepResult(ok=True))
    assert len(events) == 2
    # payload carries the step id and a status
    for _event, payload in events:
        assert payload["step_id"] == "gold"
        assert "status" in payload


# ---- Executor runner injection ------------------------------------------

def test_executor_injects_runner_into_ctx():
    seen = {}

    def _capture(ctx):
        seen["runner"] = getattr(ctx, "runner", "MISSING")
        return StepResult(ok=True)

    def runner(argv):
        return StepResult(ok=True)

    dag = Dag([Step("a", "t")])
    state = RunState(dag)

    class Reg:
        def get(self, t):
            return _capture

    Executor(Reg(), None, runner=runner).run(state)
    assert seen["runner"] is runner


# ---- integration: drive executor with sink + runner ---------------------

def test_run_executor_records_all_steps_and_invokes_runner():
    doc = FakeDoc()
    sink = run.FrappeSink(doc, now=lambda: "T")
    dag, state = run.plan_run({"skip_sync": True})

    calls = []

    def runner(argv):
        calls.append(argv)
        return StepResult(ok=True, rows=7, log=" ".join(argv))

    Executor(handlers, sink, runner=runner).run(state)

    assert state.is_success()
    recorded = {r.step_id: r for r in doc.steps}
    assert set(recorded) == {"seed", "silver", "gold", "assertions", "signoff"}
    for row in doc.steps:
        assert row.status == Status.SUCCESS
        assert row.rows == 7
    # dbt steps shelled out via the runner with a dbt argv
    assert any(argv[0] == "dbt" for argv in calls)

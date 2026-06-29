"""FrappeSink must flush the run doc per step (so the live timeline reflects
progress mid-run, not only at the end). Pure-python; uses a fake doc."""
from konsol.orchestrator.dag import Step
from konsol.orchestrator.handlers import StepResult
from konsol.orchestrator.run import FrappeSink


class _FakeDoc:
    def __init__(self):
        self.steps = []

    def append(self, field, values):
        row = dict(values)
        getattr(self, field).append(row)
        return row


def test_persist_called_on_start_and_result():
    calls = []
    sink = FrappeSink(_FakeDoc(), persist=lambda: calls.append(1), now=lambda: "t")
    step = Step(id="seed", type="dbt_seed")
    sink.on_step_start(step)
    sink.on_step_result(step, StepResult(ok=True))
    assert len(calls) == 2  # one flush per start + per result


def test_persist_optional_noop_without_callback():
    sink = FrappeSink(_FakeDoc(), now=lambda: "t")  # no persist -> must not raise
    step = Step(id="seed", type="dbt_seed")
    sink.on_step_start(step)
    sink.on_step_result(step, StepResult(ok=True))
    assert sink.run_doc.steps[0]["status"] == "Success"

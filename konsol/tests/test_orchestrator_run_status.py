"""Regression — the parent Pipeline Run.status field must accept every status
the orchestrator binding (run.py) writes to it. An integration run revealed the
binding wrote "Running"/"Success" while the legacy field only allowed
Queued/Extracting/Transforming/Completed/Failed -> ValidationError killed the job.
Pure JSON-load test; runs on host."""
import json
import os

APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # .../konsol
PRUN = os.path.join(APP, "pipeline", "doctype", "pipeline_run", "pipeline_run.json")
PSTEP = os.path.join(APP, "pipeline", "doctype", "pipeline_step", "pipeline_step.json")


def _status_options(path):
    d = json.load(open(path))
    field = [f for f in d["fields"] if f.get("fieldname") == "status"][0]
    return set((field.get("options") or "").split("\n"))


def test_pipeline_run_status_supports_orchestrator_lifecycle():
    opts = _status_options(PRUN)
    # run.py sets the parent status to Running (start) then Completed/Failed
    # (terminal); cancel_run sets Cancelled. All must be valid field options.
    for s in ["Queued", "Running", "Completed", "Failed", "Cancelled"]:
        assert s in opts, f"{s!r} missing from Pipeline Run status options: {sorted(opts)}"


def test_pipeline_step_status_supports_orchestrator_vocab():
    # FrappeSink writes orchestrator Status values to the child rows. The legacy
    # field used "Failure" (not "Failed") and lacked Cancelled -> a failed step
    # raised ValidationError mid-run. All written values must be valid options.
    opts = _status_options(PSTEP)
    for s in ["Pending", "Running", "Success", "Failed", "Skipped", "Cancelled"]:
        assert s in opts, f"{s!r} missing from Pipeline Step status options: {sorted(opts)}"

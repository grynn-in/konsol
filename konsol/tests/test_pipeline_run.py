"""TDD tests for Pipeline Run DocType."""
import json
import os

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOCTYPE_DIR = os.path.join(APP_DIR, "pipeline", "doctype", "pipeline_run")


def test_pipeline_run_json_exists():
    """Pipeline Run DocType JSON must exist."""
    path = os.path.join(DOCTYPE_DIR, "pipeline_run.json")
    assert os.path.exists(path)


def test_pipeline_run_json_valid():
    """Pipeline Run JSON must have correct metadata."""
    with open(os.path.join(DOCTYPE_DIR, "pipeline_run.json")) as f:
        doc = json.load(f)

    assert doc["name"] == "Pipeline Run"
    assert doc["doctype"] == "DocType"
    assert doc["module"] == "Pipeline"
    assert doc["issingle"] == 0


def test_pipeline_run_has_required_fields():
    """Pipeline Run must have all required fields."""
    with open(os.path.join(DOCTYPE_DIR, "pipeline_run.json")) as f:
        doc = json.load(f)

    field_names = [f["fieldname"] for f in doc["fields"]]
    required = [
        "status",
        "started_at",
        "completed_at",
        "airbyte_job_id",
        "rows_synced",
        "dbt_result",
        "error_log",
        "triggered_by",
    ]
    for fname in required:
        assert fname in field_names, f"Missing field: {fname}"


def test_pipeline_run_status_options():
    """Status field must have correct Select options."""
    with open(os.path.join(DOCTYPE_DIR, "pipeline_run.json")) as f:
        doc = json.load(f)

    status_field = next(f for f in doc["fields"] if f["fieldname"] == "status")
    assert status_field["fieldtype"] == "Select"
    options = status_field["options"].split("\n")
    expected = ["Queued", "Extracting", "Transforming", "Completed", "Failed"]
    assert options == expected


def test_pipeline_run_triggered_by_is_link():
    """triggered_by must be a Link to User."""
    with open(os.path.join(DOCTYPE_DIR, "pipeline_run.json")) as f:
        doc = json.load(f)

    field = next(f for f in doc["fields"] if f["fieldname"] == "triggered_by")
    assert field["fieldtype"] == "Link"
    assert field["options"] == "User"


def test_pipeline_run_python_exists():
    """Pipeline Run Python file must exist."""
    path = os.path.join(DOCTYPE_DIR, "pipeline_run.py")
    assert os.path.exists(path)


def test_pipeline_run_python_has_trigger_method():
    """Pipeline Run must expose a trigger_pipeline whitelisted method."""
    path = os.path.join(DOCTYPE_DIR, "pipeline_run.py")
    with open(path) as f:
        content = f.read()
    assert "trigger_pipeline" in content
    assert "whitelist" in content or "whitelisted" in content

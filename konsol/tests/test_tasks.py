"""TDD tests for tasks.py (Airbyte sync + dbt build background job)."""
import ast
import os

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TASKS_PATH = os.path.join(APP_DIR, "tasks.py")


def test_tasks_file_exists():
    """tasks.py must exist at app root."""
    assert os.path.exists(TASKS_PATH)


def test_tasks_has_run_pipeline_function():
    """tasks.py must define run_pipeline(pipeline_run)."""
    with open(TASKS_PATH) as f:
        tree = ast.parse(f.read())

    func_names = [
        node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)
    ]
    assert "run_pipeline" in func_names


def test_run_pipeline_accepts_pipeline_run_arg():
    """run_pipeline must accept a pipeline_run argument."""
    with open(TASKS_PATH) as f:
        tree = ast.parse(f.read())

    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "run_pipeline":
            arg_names = [a.arg for a in node.args.args]
            assert "pipeline_run" in arg_names
            return
    assert False, "run_pipeline function not found"


def test_tasks_has_airbyte_sync_function():
    """tasks.py must have _run_airbyte_sync helper."""
    with open(TASKS_PATH) as f:
        tree = ast.parse(f.read())

    func_names = [
        node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)
    ]
    assert "_run_airbyte_sync" in func_names


def test_tasks_has_dbt_build_function():
    """tasks.py must have _run_dbt_build helper."""
    with open(TASKS_PATH) as f:
        tree = ast.parse(f.read())

    func_names = [
        node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)
    ]
    assert "_run_dbt_build" in func_names


def test_tasks_uses_subprocess_for_dbt():
    """dbt must be run via subprocess, not shell=True."""
    with open(TASKS_PATH) as f:
        content = f.read()
    assert "subprocess" in content


def test_tasks_publishes_realtime():
    """Must publish realtime events for UI updates."""
    with open(TASKS_PATH) as f:
        content = f.read()
    assert "publish_realtime" in content


def test_tasks_handles_errors():
    """Must have try/except that sets status to Failed."""
    with open(TASKS_PATH) as f:
        content = f.read()
    assert "Failed" in content
    assert "except" in content

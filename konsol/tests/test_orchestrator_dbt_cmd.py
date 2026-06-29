"""TDD — orchestrator real handlers / dbt command builder (PRD-8). Pure-python."""
import json

from konsol.orchestrator import handlers
from konsol.orchestrator.handlers import (
    BUILTIN_TYPES,
    DBT_VERB_BY_TYPE,
    StepResult,
    build_dbt_command,
    get,
)
from konsol.orchestrator.executor import StepContext
from konsol.orchestrator.dag import Step


# ---- build_dbt_command (the pure, testable core) ----

def test_build_dbt_command_base():
    assert build_dbt_command("run", {}) == ["dbt", "run"]


def test_build_dbt_command_handles_none_params():
    assert build_dbt_command("seed", None) == ["dbt", "seed"]


def test_build_dbt_command_select():
    assert build_dbt_command("run", {"select": "consolidation"}) == [
        "dbt",
        "run",
        "--select",
        "consolidation",
    ]


def test_build_dbt_command_full_refresh():
    assert build_dbt_command("build", {"full_refresh": True}) == [
        "dbt",
        "build",
        "--full-refresh",
    ]


def test_build_dbt_command_full_refresh_false_omitted():
    assert "--full-refresh" not in build_dbt_command("run", {"full_refresh": False})


def test_build_dbt_command_vars_is_json():
    argv = build_dbt_command("run", {"vars": {"fiscal_year": 2024, "fiscal_period": 12}})
    assert argv[0:2] == ["dbt", "run"]
    assert "--vars" in argv
    payload = argv[argv.index("--vars") + 1]
    assert json.loads(payload) == {"fiscal_year": 2024, "fiscal_period": 12}


def test_build_dbt_command_vars_deterministic():
    a = build_dbt_command("run", {"vars": {"b": 2, "a": 1}})
    b = build_dbt_command("run", {"vars": {"a": 1, "b": 2}})
    assert a == b


def test_build_dbt_command_all_combined_order():
    argv = build_dbt_command(
        "build",
        {"select": "domain:consolidation", "full_refresh": True, "vars": {"fiscal_year": 2024}},
    )
    assert argv[0:2] == ["dbt", "build"]
    assert "--select" in argv and "domain:consolidation" in argv
    assert "--full-refresh" in argv
    assert "--vars" in argv


def test_empty_select_omitted():
    assert build_dbt_command("run", {"select": ""}) == ["dbt", "run"]


# ---- verb mapping ----

def test_dbt_verb_by_type():
    assert DBT_VERB_BY_TYPE == {
        "dbt_seed": "seed",
        "dbt_run": "run",
        "dbt_build": "build",
        "dbt_test": "test",
    }


# ---- handlers still resolve and stay host-safe ----

def test_all_builtin_types_still_resolve():
    for t in BUILTIN_TYPES:
        assert callable(get(t))


def test_builtin_handlers_ok_with_bare_dict_ctx():
    # mirrors the existing host suite: get(t)({}) must not crash and returns ok
    for t in BUILTIN_TYPES:
        result = get(t)({})
        assert isinstance(result, StepResult)
        assert result.ok is True


def test_dbt_handler_builds_command_into_log():
    step = Step(id="gold", type="dbt_run", params={"select": "consolidation", "full_refresh": True})
    ctx = StepContext(step)
    result = get("dbt_run")(ctx)
    assert result.ok is True
    assert "dbt run" in result.log
    assert "--select consolidation" in result.log
    assert "--full-refresh" in result.log


def test_dbt_seed_handler_uses_seed_verb():
    step = Step(id="seed", type="dbt_seed", params={})
    result = get("dbt_seed")(StepContext(step))
    assert result.log.startswith("dbt seed")


def test_dbt_handler_runs_injected_runner():
    captured = {}

    def runner(argv):
        captured["argv"] = argv
        return StepResult(ok=True, rows=99, log="ran")

    step = Step(id="gold", type="dbt_build", params={"select": "x"})
    ctx = StepContext(step)
    ctx.runner = runner
    result = get("dbt_build")(ctx)
    assert captured["argv"] == ["dbt", "build", "--select", "x"]
    assert result.rows == 99


def test_airbyte_sync_handler_host_safe():
    result = get("airbyte_sync")(StepContext(Step(id="extract", type="airbyte_sync")))
    assert result.ok is True

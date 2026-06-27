"""TDD — orchestrator handler registry (PRD-4). Pure-python."""
import pytest

from konsol.orchestrator import handlers
from konsol.orchestrator.handlers import (
    BUILTIN_TYPES,
    Handler,
    StepResult,
    get,
    register,
)


def test_step_result_defaults():
    r = StepResult(ok=True)
    assert r.ok is True
    assert r.rows == 0
    assert r.log == ""
    assert r.error == ""


def test_step_result_fields():
    r = StepResult(ok=False, rows=42, log="ran", error="boom")
    assert (r.ok, r.rows, r.log, r.error) == (False, 42, "ran", "boom")


def test_register_and_lookup():
    @register("unit_test_type")
    def _h(ctx):
        return StepResult(ok=True, rows=7)

    h = get("unit_test_type")
    assert h is _h
    assert h({}).rows == 7
    # cleanup so re-runs / other tests are not polluted
    handlers._REGISTRY.pop("unit_test_type", None)


def test_unknown_type_raises():
    with pytest.raises(KeyError):
        get("no_such_handler_type")


def test_duplicate_registration_raises():
    @register("dup_type")
    def _h(ctx):
        return StepResult(ok=True)

    with pytest.raises(ValueError):
        @register("dup_type")
        def _h2(ctx):
            return StepResult(ok=True)

    handlers._REGISTRY.pop("dup_type", None)


def test_builtin_types_registered():
    expected = {
        "airbyte_sync",
        "dbt_seed",
        "dbt_run",
        "dbt_build",
        "dbt_test",
        "close_assertions",
        "signoff",
    }
    assert expected <= set(BUILTIN_TYPES)
    for t in expected:
        assert get(t) is not None


def test_builtin_stub_returns_ok():
    for t in BUILTIN_TYPES:
        result = get(t)({})
        assert isinstance(result, StepResult)
        assert result.ok is True


def test_handler_protocol_runtime_checkable():
    def good(ctx):
        return StepResult(ok=True)

    assert isinstance(good, Handler)
    assert not isinstance(123, Handler)

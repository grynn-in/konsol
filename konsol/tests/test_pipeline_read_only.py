"""Pipeline definitions are read-only; the stub signoff step is gone; the
orchestrator's writing endpoints are POST-only.

Decided by Deepak Pai, 22 Nov 2026 (option A). Site-free: JSON and AST
assertions plus the pure handler registry.
"""
import ast
import json
import os

from konsol.orchestrator import handlers, lineage, plan
from konsol.orchestrator.handlers import StepResult

APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _json(*parts):
    with open(os.path.join(APP, *parts)) as fh:
        return json.load(fh)


# -- permissions ------------------------------------------------------------

def test_pipeline_grants_no_create_write_or_delete():
    perms = _json("pipeline", "doctype", "pipeline", "pipeline.json")["permissions"]
    assert {p["role"] for p in perms if p.get("read")} == {"System Manager", "EPM Admin"}
    for p in perms:
        for right in ("create", "write", "delete"):
            assert not p.get(right), f"{p['role']} holds {right} on Pipeline"


def test_pipeline_step_grants_no_create_write_or_delete():
    doc = _json("pipeline", "doctype", "pipeline_step", "pipeline_step.json")
    for p in doc.get("permissions", []):
        for right in ("create", "write", "delete"):
            assert not p.get(right), f"{p['role']} holds {right} on Pipeline Step"


# -- the signoff step is gone ----------------------------------------------

def test_fixture_has_no_signoff_step():
    fixture = _json("fixtures", "pipeline.json")
    for pipeline in fixture:
        for step in pipeline["steps"]:
            assert step["step_type"] != "signoff"
            assert step["step_id"] != "signoff"
        assert "signoff" not in pipeline.get("description", "")
    group_close = next(p for p in fixture if p["name"] == "Group Close")
    assert [s["step_id"] for s in group_close["steps"]][-1] == "assertions"


def test_step_type_options_have_no_signoff():
    doc = _json("pipeline", "doctype", "pipeline_step", "pipeline_step.json")
    field = next(f for f in doc["fields"] if f["fieldname"] == "step_type")
    assert "signoff" not in field["options"].split("\n")


def test_default_definition_has_no_signoff():
    assert [s.id for s in plan.DEFAULT_DEFINITION][-1] == "assertions"
    assert all(s.type != "signoff" for s in plan.DEFAULT_DEFINITION)
    assert "signoff" not in lineage.STEP_OUTPUTS


def test_runner_has_no_signoff_branch():
    src = open(os.path.join(APP, "orchestrator", "run.py")).read()
    assert "_run_signoff" not in src
    assert "signed off" not in src


def test_a_signoff_step_is_refused_by_name():
    result = handlers.get("signoff")({})
    assert isinstance(result, StepResult)
    assert result.ok is False
    assert "signoff" in result.error
    assert "removed" in result.error


def test_a_signoff_step_is_refused_even_with_a_runner():
    result = handlers.get("signoff")({"runner": lambda argv: StepResult(ok=True)})
    assert result.ok is False and "signoff" in result.error


# -- writing endpoints are POST-only ---------------------------------------

def _whitelist_methods(fn):
    for dec in fn.decorator_list:
        if isinstance(dec, ast.Call) and getattr(dec.func, "id", None) == "whitelist":
            for kw in dec.keywords:
                if kw.arg == "methods":
                    return ast.literal_eval(kw.value)
            return None
    raise AssertionError(f"{fn.name} is not whitelisted")


def _api_functions():
    tree = ast.parse(open(os.path.join(APP, "orchestrator", "api.py")).read())
    return {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}


def test_writing_endpoints_are_post_only():
    fns = _api_functions()
    for name in ("start_run", "retry_step", "resume_run", "cancel_run"):
        assert _whitelist_methods(fns[name]) == ["POST"], name


def test_reading_endpoints_stay_get():
    fns = _api_functions()
    for name in ("get_run", "launch_options"):
        assert _whitelist_methods(fns[name]) is None, name


def test_host_whitelist_shim_accepts_methods_kwarg():
    from konsol.orchestrator import api

    @api.whitelist(methods=["POST"])
    def f():
        return 1

    assert f() == 1

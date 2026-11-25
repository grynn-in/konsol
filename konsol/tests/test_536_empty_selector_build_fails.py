"""A governed build whose selector matches no models fails (#536).

dbt exits 0 on "does not match any enabled nodes / Nothing to do", so the
Build Approval ended Completed with nothing built (a stale warehouse checkout
that lacks the tag). With a selector, zero model nodes built is Failed, with a
reason the operator reads in Build Approval.error_message (and the Pipeline
Run's error_log). A full build, and a selector build that built a model, are
unchanged.

frappe is stubbed (StartSite from test_failed_start_follow_up); the live A/B
is in the PR.
"""
import importlib.util
import json
import os
import tempfile
import types

HERE = os.path.dirname(os.path.abspath(__file__))
REASON = "selector matched no models: is the warehouse checkout current?"
NO_MATCH = "The selection criterion 'tag:domain:allocation' does not match any enabled nodes\nNothing to do. Try checking your model configs and model specification args"

_spec = importlib.util.spec_from_file_location("ffs_536", os.path.join(HERE, "test_failed_start_follow_up.py"))
ffs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ffs)


def build(scope, results=None, stdout="", write_results=True):
    """Run run_governed_build for ``scope`` with dbt stubbed. ``results`` is the
    list of run_results.json unique_ids dbt wrote."""
    site = ffs.StartSite()
    site.committed[("Build Approval", "BA-1")]["build_scope"] = scope
    project = tempfile.mkdtemp()

    def job(tasks):
        tasks._preflight_check = lambda s: (True, "ok")
        tasks.request_build_for_scope = lambda *a, **k: site.requests.append(a)
        tasks.frappe.get_single = lambda name: types.SimpleNamespace(dbt_project_path=project)

        def run(*a, **k):
            if write_results:
                os.makedirs(os.path.join(project, "target"), exist_ok=True)
                with open(os.path.join(project, "target", "run_results.json"), "w") as fh:
                    json.dump({"results": [{"unique_id": u, "status": "success"} for u in results or []]}, fh)
            return types.SimpleNamespace(returncode=0, stdout=stdout, stderr="")
        tasks.subprocess = types.SimpleNamespace(run=run, TimeoutExpired=TimeoutError)
        tasks.run_governed_build("BA-1")
    assert site.start(call=job) is None
    return site.committed[("Build Approval", "BA-1")], site.committed[("Pipeline Run", "PR-1")]


def test_selector_with_no_models_fails_with_the_reason():
    ba, run = build("staging", results=[], stdout=NO_MATCH)
    assert ba["workflow_state"] == "Failed" and ba["error_message"] == REASON
    assert run["status"] == "Failed" and run["error_log"] == REASON


def test_a_test_only_result_is_still_no_models():
    ba, _ = build("staging", results=["test.konsol.not_null_x"], stdout="")
    assert ba["workflow_state"] == "Failed" and ba["error_message"] == REASON


def test_selector_without_run_results_falls_back_to_the_dbt_message():
    ba, _ = build("staging", stdout=NO_MATCH, write_results=False)
    assert ba["workflow_state"] == "Failed" and ba["error_message"] == REASON


def test_selector_that_built_a_model_is_completed():
    ba, run = build("staging", results=["model.konsol.stg_a", "test.konsol.t"], stdout="Done.")
    assert ba["workflow_state"] == "Completed" and not ba["error_message"]
    assert run["status"] == "Completed"


def test_full_build_with_no_models_is_unchanged():
    ba, run = build("full", results=[], stdout=NO_MATCH)
    assert ba["workflow_state"] == "Completed" and not ba["error_message"]
    assert run["status"] == "Completed"

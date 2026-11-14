"""The dbt build argv (konsol#195), pure: konsol/build_command.py.

A scoped build must pass ``--indirect-selection cautious`` so dbt does not
select tests whose other parents the build did not touch; a full build (no
selector) is the plain six-element command. The module imports no frappe.
"""
from konsol.build_command import dbt_build_command

DBT, PROJECT = "/opt/dbt/bin/dbt", "/srv/konsol/dbt"
BASE = [DBT, "build", "--project-dir", PROJECT, "--profiles-dir", PROJECT]


def test_full_build_has_no_selector_and_no_indirect_selection():
    cmd = dbt_build_command(DBT, PROJECT, None)
    assert cmd == BASE
    assert "--select" not in cmd
    assert "--indirect-selection" not in cmd


def test_scoped_build_adds_select_then_cautious_indirect_selection():
    cmd = dbt_build_command(DBT, PROJECT, "+tag:domain:consolidation")
    assert cmd == BASE + ["--select", "+tag:domain:consolidation", "--indirect-selection", "cautious"]


def test_empty_selector_behaves_like_none():
    assert dbt_build_command(DBT, PROJECT, "") == dbt_build_command(DBT, PROJECT, None) == BASE


def test_result_is_a_fresh_list_each_call():
    first = dbt_build_command(DBT, PROJECT, None)
    first.append("--fail-fast")
    assert dbt_build_command(DBT, PROJECT, None) == BASE
    scoped = dbt_build_command(DBT, PROJECT, "tag:x")
    scoped.clear()
    assert dbt_build_command(DBT, PROJECT, "tag:x")[:6] == BASE


# konsol#261: a schema-changing governed build (Dimension publish/unpublish)
# does not pass --full-refresh, so incremental gold models (append strategy,
# with a pre_hook DELETE) rebuild against the OLD schema and are left at 0
# rows (measured: 47,308 -> 0; only --full-refresh recovers). Decision
# (Deepak Pai, 19 Sep 2026, option A): a full_refresh Check field on Build
# Approval, read by run_governed_build and passed here explicitly.

def test_full_refresh_true_adds_the_flag_to_a_full_build():
    cmd = dbt_build_command(DBT, PROJECT, None, full_refresh=True)
    assert cmd == BASE + ["--full-refresh"]


def test_full_refresh_false_adds_no_flag():
    assert dbt_build_command(DBT, PROJECT, None, full_refresh=False) == BASE


def test_full_refresh_default_adds_no_flag():
    """Existing calls (no full_refresh kwarg) must keep behaving exactly as
    before: no --full-refresh."""
    assert dbt_build_command(DBT, PROJECT, None) == BASE
    assert "--full-refresh" not in dbt_build_command(DBT, PROJECT, None)


def test_full_refresh_composes_with_a_selector():
    cmd = dbt_build_command(DBT, PROJECT, "+tag:domain:consolidation", full_refresh=True)
    assert cmd == BASE + [
        "--select", "+tag:domain:consolidation", "--indirect-selection", "cautious",
        "--full-refresh",
    ]


def test_full_refresh_result_is_still_a_fresh_list_each_call():
    first = dbt_build_command(DBT, PROJECT, None, full_refresh=True)
    first.append("--fail-fast")
    assert dbt_build_command(DBT, PROJECT, None, full_refresh=True) == BASE + ["--full-refresh"]


# K9a (konsol#269): an isolated Build Scope is built only by its own scope; every
# other build excludes its tag. The frappe read is stubbed here.
import ast
import os

from konsol import build_command as _bc
from konsol.orchestrator.handlers import build_dbt_command

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.realpath(__file__))))
EXCL = ["--exclude", "tag:domain:allocation"]


def _stub(monkeypatch_scopes):
    saved = _bc.isolated_scopes
    _bc.isolated_scopes = lambda: list(monkeypatch_scopes)
    return saved


def _with_isolated(scopes, fn):
    saved = _stub(scopes)
    try:
        return fn()
    finally:
        _bc.isolated_scopes = saved


def test_excludes_isolated():
    def run():
        full = dbt_build_command(DBT, PROJECT, None)
        chart = dbt_build_command(DBT, PROJECT, "+tag:domain:chart", scope="chart")
        cons = dbt_build_command(DBT, PROJECT, "+tag:domain:consolidation", scope="consolidation")
        alloc = dbt_build_command(DBT, PROJECT, "tag:domain:allocation", scope="allocation")
        assert full == BASE + EXCL
        assert chart[-2:] == EXCL and cons[-2:] == EXCL
        assert "--exclude" not in alloc
        assert _bc.isolated_excludes("allocation") == []
        assert _bc.isolated_excludes("chart") == EXCL
    _with_isolated(["allocation"], run)


def test_no_isolated_scopes_leaves_argv_unchanged():
    def run():
        assert dbt_build_command(DBT, PROJECT, None) == BASE
        assert dbt_build_command(DBT, PROJECT, "tag:x", scope="x") == BASE + ["--select", "tag:x", "--indirect-selection", "cautious"]
        assert build_dbt_command("build", {"select": "c"}) == ["dbt", "build", "--select", "c", "--indirect-selection", "cautious"]
    _with_isolated([], run)


def test_several_isolated_scopes_each_excluded_except_own():
    def run():
        assert _bc.isolated_excludes(None) == ["--exclude", "tag:domain:a", "--exclude", "tag:domain:b"]
        assert _bc.isolated_excludes("a") == ["--exclude", "tag:domain:b"]
    _with_isolated(["a", "b"], run)


def test_orchestrator_builder_excludes_isolated():
    def run():
        assert build_dbt_command("build", {}) == ["dbt", "build"] + EXCL
        assert build_dbt_command("run", {"select": "x", "build_scope": "chart"})[-2:] == EXCL
        assert "--exclude" not in build_dbt_command("build", {"select": "y", "build_scope": "allocation"})
        assert "--exclude" not in build_dbt_command("seed", {})
    _with_isolated(["allocation"], run)


def test_tasks_run_dbt_build_uses_the_shared_builder():
    src = open(os.path.join(_ROOT, "konsol", "tasks.py")).read()
    fn = next(n for n in ast.walk(ast.parse(src)) if isinstance(n, ast.FunctionDef) and n.name == "_run_dbt_build")
    calls = [c.func.id for c in ast.walk(fn) if isinstance(c, ast.Call) and isinstance(c.func, ast.Name)]
    assert "dbt_build_command" in calls


def test_build_scope_has_isolated_check_default_off():
    import json
    p = os.path.join(_ROOT, "konsol", "pipeline", "doctype", "build_scope", "build_scope.json")
    f = {x["fieldname"]: x for x in json.load(open(p))["fields"]}["isolated"]
    assert f["fieldtype"] == "Check" and f["label"] == "Isolated" and str(f["default"]) == "0"
    assert f["description"] == "Built only by its own scope; every other build excludes it."

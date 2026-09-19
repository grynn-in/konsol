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

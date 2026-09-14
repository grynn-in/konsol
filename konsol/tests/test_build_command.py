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

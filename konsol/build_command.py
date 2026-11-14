"""The argv for a governed dbt build (konsol#195).

dbt's default ``--indirect-selection eager`` selects a test when ANY of its
parents is selected, so a scoped build ran tests whose other parents it had not
built and they failed against empty tables. ``cautious`` selects a test only
when ALL of its parents are selected, so a scoped build tests just what it
built.

A full build has no selector and is unchanged: no ``--select``, no
``--indirect-selection``. This module imports no frappe.
"""


def isolated_scopes():
    """Names of the Build Scopes flagged ``isolated`` (K9a, konsol#269).

    The only frappe read in this module; host tests stub it. Where frappe is
    not importable (host tests, pure planning) there are no scopes to read.
    A database error is not caught.
    """
    try:
        import frappe
    except ImportError:
        return []
    return frappe.get_all("Build Scope", filters={"isolated": 1}, pluck="name", order_by="name asc")


def isolated_excludes(scope):
    """``--exclude tag:domain:<s>`` for every isolated scope ``s`` other than ``scope``.

    An isolated scope is built only by its own scope; every other build
    (full, chart, consolidation, ...) excludes it. Pure given ``isolated_scopes``.
    """
    argv = []
    for s in isolated_scopes():
        if s != scope:
            argv.extend(["--exclude", f"tag:domain:{s}"])
    return argv


def dbt_build_command(dbt_bin, project_path, selector, full_refresh=False, scope=None):
    """Return a new argv list for ``dbt build`` on ``project_path``.

    ``selector`` is a dbt selector (``"+tag:domain:consolidation"``) or
    ``None``/``""`` for a full build.

    ``full_refresh`` (konsol#261) appends ``--full-refresh``. Incremental gold
    models use ``incremental_strategy='append'`` with a pre_hook ``DELETE``;
    after a schema change that delete-then-append fails on a column-count
    mismatch and leaves the table at 0 rows, and only a full refresh rebuilds
    it from scratch. Defaults to ``False`` so every existing caller is
    unchanged.

    ``scope`` is the requested Build Scope (None for a full build); isolated
    scopes other than it are excluded.
    """
    cmd = [dbt_bin, "build", "--project-dir", project_path, "--profiles-dir", project_path]
    if selector:
        cmd.extend(["--select", selector, "--indirect-selection", "cautious"])
    cmd.extend(isolated_excludes(scope))
    if full_refresh:
        cmd.append("--full-refresh")
    return cmd

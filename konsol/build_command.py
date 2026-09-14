"""The argv for a governed dbt build (konsol#195).

dbt's default ``--indirect-selection eager`` selects a test when ANY of its
parents is selected, so a scoped build ran tests whose other parents it had not
built and they failed against empty tables. ``cautious`` selects a test only
when ALL of its parents are selected, so a scoped build tests just what it
built.

A full build has no selector and is unchanged: no ``--select``, no
``--indirect-selection``. This module imports no frappe.
"""


def dbt_build_command(dbt_bin, project_path, selector):
    """Return a new argv list for ``dbt build`` on ``project_path``.

    ``selector`` is a dbt selector (``"+tag:domain:consolidation"``) or
    ``None``/``""`` for a full build.
    """
    cmd = [dbt_bin, "build", "--project-dir", project_path, "--profiles-dir", project_path]
    if selector:
        cmd.extend(["--select", selector, "--indirect-selection", "cautious"])
    return cmd

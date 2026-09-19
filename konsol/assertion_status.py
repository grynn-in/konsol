"""How a dbt test result becomes Assertion Run / Assertion Step state (konsol#265).

A dbt test configured ``severity='warn'`` reports status ``warn`` and dbt exits
0. Before konsol#265 ``warn`` was absent from the status map and fell through
its default to ``Error``: the run turned Red, ``sign_off_close`` then blocked
the close unless an EPM Admin supplied an override reason, and the offending
rows were dropped because ``relation_name`` was only captured for ``Fail``. A
warning cost more friction than an error and showed less — and 20 of 126
singular assertions are warn-severity, including
``assert_trial_balance_balances``.

Decision (Deepak Pai, 19 Sep 2026, konsol#265): **option C**. A run with
warnings and no failures is **Amber**, and signing it off requires a typed
acknowledgement, so the close record carries what was outstanding *and* why it
was signed anyway. Rejected: folding warn into Green (silently decides that
warnings never gate a close) and recording the warnings automatically without
an acknowledgement (says what, never why).

This module imports no frappe — the ``konsol.build_command`` idiom from
konsol#195 — so the mapping is unit-tested on the host.
"""

#: dbt's ``run_results.json`` status -> Assertion Step status.
DBT_TO_STATUS = {
    "pass": "Pass",
    "fail": "Fail",
    "warn": "Warn",
    "error": "Error",
}

#: Statuses for which ``--store-failures`` wrote a table of offending rows.
#: An Error produced no result set, and a Pass has nothing to show.
ROW_BEARING = ("Fail", "Warn")


def step_status(raw_status):
    """Map a dbt result status to an Assertion Step status.

    An unrecognised status maps to ``Error`` on purpose: something the product
    does not understand must not be reported as benign.
    """
    return DBT_TO_STATUS.get((raw_status or "").lower(), "Error")


def captures_rows(status):
    """Whether the ``--store-failures`` rows should be fetched for this status."""
    return status in ROW_BEARING


def run_status(passed, failed, errored, warned):
    """The Assertion Run status for a set of counters.

    Red beats Amber beats Green, so a warning can never mask a failure. A run
    that asserted nothing stays Red — pre-existing behaviour, kept: an empty
    suite is not a passing close.
    """
    if failed or errored:
        return "Red"
    if (passed + warned) == 0:
        return "Red"
    if warned:
        return "Amber"
    return "Green"


def severity_of(unique_id, manifest_nodes, status):
    """The severity dbt actually ran a test at.

    ``run_results.json`` does not carry severity, so it is read from the
    manifest. Falling back to the literal ``"error"`` is what made the Assertion
    Step's ``severity`` field useless, so the fallback reads the observed status
    instead.

    The fallback is a best guess, not an invariant: a test configured
    ``severity: error`` with an ``error_if`` threshold reports ``warn`` below
    that threshold, so a ``Warn`` status does not prove warn severity. It is
    still the better guess — dbt exited 0 and the run is Amber either way — but
    it is why the manifest is preferred whenever it can be read, and a passing
    warn-severity test cannot be identified without it.
    """
    node = (manifest_nodes or {}).get(unique_id) or {}
    declared = ((node.get("config") or {}).get("severity") or "").lower()
    if declared in ("warn", "error"):
        return declared
    return "warn" if status == "Warn" else "error"

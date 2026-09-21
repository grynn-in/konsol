"""A trial-balance-only site builds without Airbyte (konsol#182, decided 13 Sep 2026).

The canonical path is a trial balance uploaded to konsol; on submit its rows land
in epm_raw.trial_balance_submissions and are claimed in the control table. Every
raw-dependent build scope (consolidation among them) ran
tasks.check_raw_data_available in preflight, which knew only connectors and
Airbyte and refused such a site with "Airbyte has never synced".

Review of #183: the trial balance check comes AFTER the connector gate (a
connector mid-sync or failed still blocks), and it counts the rows in the
warehouse, not MariaDB's docstatus (a wiped ClickHouse volume with submitted
documents still in MariaDB has nothing to build from).

The two functions run here, lifted out of tasks.py with ast and executed against
a stub frappe and a stub konsol.clickhouse (tasks.py imports the Airbyte client
at module level).
"""
import ast
import os
import sys
import types

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TASKS = os.path.join(APP_DIR, "tasks.py")
NEVER = (False, "Airbyte has never synced — epm_raw may be empty")


class _Row(dict):
    __getattr__ = dict.get


def check(skip=0, rows=0, connectors=(), sync_at=None, sync_status=None, warehouse_error=None, mariadb_tbs=0,
          without_basis="0", basis_error=None):
    """check_raw_data_available() against ``rows`` claimed trial balance rows in
    the warehouse. ``mariadb_tbs`` submitted documents exist in MariaDB, which
    must not matter. ``without_basis`` is what the konsolidat#199 basis query
    returns ("<count>\t<names>"; "0" = every claimed batch declares one);
    ``basis_error`` is raised by that query alone (the rows query answered)."""
    settings = _Row(skip_airbyte_sync=skip, last_airbyte_sync_status=sync_status, last_airbyte_sync_at=sync_at,
                    last_airbyte_sync_rows=7)
    sqls = []

    def execute(sql, params=None):
        sqls.append(sql)
        if warehouse_error is not None:
            raise warehouse_error
        if "amount_basis" in sql:
            if basis_error is not None:
                raise basis_error
            return without_basis
        return str(rows)

    def get_all(doctype, filters=None, fields=None, limit_page_length=None):
        assert doctype == "Connector"
        return [_Row(c) for c in connectors if c.get("enabled", 1)]

    def exists(doctype, filters=None):
        return mariadb_tbs > 0 if doctype == "Trial Balance Submission" else False

    frappe = types.SimpleNamespace(get_single=lambda name: settings, get_all=get_all,
                                   db=types.SimpleNamespace(table_exists=lambda name: True, exists=exists,
                                                            count=lambda *a, **k: mariadb_tbs))
    with open(TASKS) as f:
        tree = ast.parse(f.read())
    wanted = {"check_raw_data_available", "_trial_balance_rows", "_connector_sync_gate", "_batches_without_basis",
              "_basis_refusal"}
    nodes = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in wanted]
    assert {n.name for n in nodes} == wanted
    ch = types.ModuleType("konsol.clickhouse")
    ch.execute = execute
    saved = sys.modules.get("konsol.clickhouse")
    sys.modules["konsol.clickhouse"] = ch
    try:
        ns = {"frappe": frappe}
        exec(compile(ast.Module(body=nodes, type_ignores=[]), TASKS, "exec"), ns)
        return ns["check_raw_data_available"](), sqls
    finally:
        if saved is None:
            sys.modules.pop("konsol.clickhouse", None)
        else:
            sys.modules["konsol.clickhouse"] = saved


RUNNING = [{"connector_name": "ZZ ERP", "last_sync_at": "2026-09-01 00:00:00", "last_sync_status": "Running"}]
FAILED = [{"connector_name": "ZZ ERP", "last_sync_at": "2026-09-01 00:00:00", "last_sync_status": "Failed"}]
NEVER_SYNCED = [{"connector_name": "ZZ ERP", "last_sync_at": None, "last_sync_status": None}]
SYNCED = [{"connector_name": "ZZ ERP", "last_sync_at": "2026-09-01 00:00:00", "last_sync_status": "Success"}]


def test_a_tb_only_site_builds_without_airbyte():
    (ok, message), _ = check(rows=12)
    assert ok and "12 trial balance rows" in message and "no connector" in message, message


def test_a_running_or_failed_connector_no_longer_blocks():
    """konsolidat#235 Phase 1 / konsol#200. Connector state is no longer a
    readiness test. Every ERP table on the live stack is empty and
    silver_gl_entries (47,308) exactly equals silver_tb_movements (47,308) —
    100% of the ledger already comes from the trial-balance path, so the gate
    was guarding an empty road while refusing builds with valid rows."""
    for connectors in (RUNNING, FAILED, NEVER_SYNCED):
        (ok, message), _ = check(rows=12, connectors=connectors)
        assert ok, message
        assert "12 trial balance rows" in message, message


def test_the_warehouse_is_asked_only_about_trial_balances():
    """No connector query, no Airbyte query — the claimed rows and their bases
    are the whole question."""
    _, sqls = check(rows=12, connectors=RUNNING)
    assert sqls and all("epm_raw.trial_balance_submission" in s for s in sqls), sqls


def test_a_wiped_warehouse_refuses_even_with_submitted_documents():
    """The ClickHouse volume was wiped; MariaDB still holds submitted trial
    balances. Nothing to build from, and the refusal says so."""
    (ok, message), _ = check(rows=0, mariadb_tbs=3)
    assert ok is False
    assert "trial balance" in message.lower(), message
    assert "airbyte" not in message.lower(), message


def test_counts_the_claimed_rows_in_the_warehouse():
    _, sqls = check(rows=5)
    assert sqls[0] == ("SELECT count() FROM epm_raw.trial_balance_submissions WHERE batch_id IN "
                       "(SELECT batch_id FROM epm_raw.trial_balance_submission_control)")
    # konsolidat#199: with claimed rows, the one other query asks which batches lack a basis
    assert len(sqls) == 2 and "argMax(amount_basis, claimed_at)" in sqls[1]


def test_an_unreadable_warehouse_refuses():
    for err in (RuntimeError("Code: 60. Unknown table (UNKNOWN_TABLE)"), ConnectionError("refused")):
        (ok, message), _ = check(warehouse_error=err)
        assert ok is False and "trial balance" in message.lower(), message


def test_no_rows_refuses_by_naming_trial_balances():
    """The old refusal was "Airbyte has never synced — epm_raw may be empty",
    which told a trial-balance-only operator to go and look at something their
    site does not use."""
    (ok, message), _ = check()
    assert ok is False
    assert "trial balance" in message.lower(), message
    assert "airbyte" not in message.lower() and "connector" not in message.lower(), message


# ---------------------------------------------------------------------------
# konsol#200: the global EPM Settings Airbyte status, set by the webhook with
# no Connector doctype required, used to gate BEFORE the trial-balance pass —
# so one claimed row could not save a build the webhook had marked failed.
# It is no longer consulted at all.
# ---------------------------------------------------------------------------
def test_a_failed_or_running_global_sync_no_longer_blocks():
    for status in ("Failed", "Running"):
        (ok, message), _ = check(rows=12, sync_status=status, sync_at="2026-09-01 00:00:00")
        assert ok, message
        assert "12 trial balance rows" in message, message


def test_an_empty_global_sync_status_with_trial_balances_still_passes():
    (ok, message), _ = check(rows=12, sync_status=None, sync_at=None)
    assert ok and "12 trial balance rows" in message, message


def test_a_synced_airbyte_alone_is_not_readiness():
    """Previously an Airbyte sync with zero claimed rows passed the preflight
    on "Airbyte sync OK". With the ERP path gone there is nothing for that
    build to read, so it must refuse."""
    (ok, message), _ = check(rows=0, sync_at="2026-09-01 00:00:00", sync_status="Succeeded")
    assert ok is False and "trial balance" in message.lower(), message


def test_the_skip_flag_is_retired():
    """skip_airbyte_sync was the escape hatch from a gate that no longer
    exists; leaving a toggle that silently changes readiness is the silent
    fallback this fix removes."""
    src = open(TASKS, encoding="utf-8").read()
    assert "skip_airbyte_sync" not in src, "the escape hatch outlived its gate"
    # and it must not be reachable through the preflight either
    (ok, message), _ = check(skip=1, rows=0)
    assert ok is False, "the retired flag still waves a build through with nothing landed"


def test_the_connector_gate_is_gone():
    src = open(TASKS, encoding="utf-8").read()
    assert "_connector_sync_gate" not in src
    assert "last_airbyte_sync_status" not in src


def test_claimed_batches_without_a_basis_are_refused_by_name():
    """konsolidat#199: rows exist, but two claimed batches never declared what
    their amounts are; the build is refused and the message names them — even
    with skip_airbyte_sync on, which is the trial-balance-only site."""
    (ok, msg), _ = check(skip=1, rows=500, without_basis="2\tTBS-0001,TBS-0002")
    assert ok is False
    assert "2 claimed trial balance batch(es) have no Amount Basis" in msg
    assert "TBS-0001, TBS-0002" in msg and "Set Amount Basis" in msg


def test_every_batch_declared_passes():
    (ok, msg), _ = check(skip=1, rows=500, without_basis="0")
    assert ok is True and "skip_airbyte_sync" in msg


def test_only_a_missing_column_means_run_bench_migrate():
    """konsolidat#199 (PR #201 review, finding 4): the rows query answered, so
    the warehouse is up; if the basis query then fails for any reason OTHER
    than the control table lacking the column, "run bench migrate" would send
    the operator on a wild goose chase. The refusal must carry the real error."""
    (ok, msg), sqls = check(rows=12, basis_error=ConnectionError("refused"))
    assert ok is False and len(sqls) == 2, (msg, sqls)
    assert msg.startswith("could not read the amount bases"), msg
    assert "refused" in msg and "bench migrate" not in msg, msg
    # the column really is missing (an old stack): the way out is named
    (ok, msg), _ = check(rows=12, basis_error=RuntimeError("Code: 47. DB::Exception: Missing columns: 'amount_basis'"))
    assert (ok, msg) == (False, "epm_raw.trial_balance_submission_control has no amount_basis column: run bench migrate")

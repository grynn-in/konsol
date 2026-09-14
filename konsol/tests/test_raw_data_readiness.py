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


def test_a_running_or_failed_connector_still_blocks():
    """One submitted trial balance must not let a build run on half-synced ERP data."""
    assert check(rows=12, connectors=RUNNING)[0] == (
        False, "Connector 'ZZ ERP' sync status is 'Running' — cannot build from raw")
    assert check(rows=12, connectors=FAILED)[0][0] is False
    assert check(rows=12, connectors=NEVER_SYNCED)[0] == (
        False, "Connector 'ZZ ERP' has never synced — epm_raw may be empty")
    # konsolidat#199: the warehouse is asked only about claimed trial balances
    # (rows, undeclared bases) before the connector decides; nothing else
    assert all("epm_raw.trial_balance_submission" in s for s in check(rows=12, connectors=RUNNING)[1])


def test_synced_connectors_are_unchanged():
    (ok, message), _ = check(rows=0, connectors=SYNCED)
    assert ok and message == "All 1 enabled connectors synced OK"


def test_a_wiped_warehouse_refuses_even_with_submitted_documents():
    """The ClickHouse volume was wiped; MariaDB still holds submitted trial balances."""
    assert check(rows=0, mariadb_tbs=3)[0] == NEVER


def test_counts_the_claimed_rows_in_the_warehouse():
    _, sqls = check(rows=5)
    assert sqls[0] == ("SELECT count() FROM epm_raw.trial_balance_submissions WHERE batch_id IN "
                       "(SELECT batch_id FROM epm_raw.trial_balance_submission_control)")
    # konsolidat#199: with claimed rows, the one other query asks which batches lack a basis
    assert len(sqls) == 2 and "argMax(amount_basis, claimed_at)" in sqls[1]


def test_an_unreadable_warehouse_refuses():
    assert check(warehouse_error=RuntimeError("Code: 60. Unknown table (UNKNOWN_TABLE)"))[0] == NEVER
    assert check(warehouse_error=ConnectionError("refused"))[0] == NEVER


def test_with_nothing_landed_the_airbyte_gate_is_unchanged():
    assert check()[0] == NEVER
    (ok, message), _ = check(sync_at="2026-09-01 00:00:00", sync_status="Succeeded")
    assert ok and message.startswith("Airbyte sync OK"), message
    assert check(sync_at="2026-09-01 00:00:00", sync_status="Failed")[0][0] is False


def test_the_flag_still_works():
    (ok, message), sqls = check(skip=1, connectors=RUNNING)
    # konsolidat#199: the skip path still asks the warehouse about claimed batches (rows, bases); no connector is consulted
    assert ok and "skip_airbyte_sync" in message and len(sqls) <= 2 and all("epm_raw" in s for s in sqls), message


# ---------------------------------------------------------------------------
# konsol#182 regression: the global EPM Settings Airbyte status (set by the
# webhook, api.py ~1564, with no Connector doctype/enabled connector required)
# must gate BEFORE the trial-balance pass. One claimed trial-balance row must
# not let a consolidation build run on a feed the webhook marked failed or
# still mid-sync.
# ---------------------------------------------------------------------------
def test_a_failed_global_sync_blocks_even_with_submitted_trial_balances():
    (ok, message), _ = check(rows=12, sync_status="Failed", sync_at="2026-09-01 00:00:00")
    assert ok is False and "Failed" in message, message


def test_a_running_global_sync_blocks_even_with_submitted_trial_balances():
    (ok, message), _ = check(rows=12, sync_status="Running", sync_at="2026-09-01 00:00:00")
    assert ok is False and "Running" in message, message


def test_a_stuck_global_sync_status_names_the_way_out():
    """A site that stopped using Airbyte can be left at Failed or Running for
    good (no final webhook); the refusal says how to get past it."""
    for status in ("Failed", "Running"):
        (ok, message), _ = check(rows=12, sync_status=status, sync_at="2026-09-01 00:00:00")
        assert ok is False and "Skip Airbyte Sync" in message, message


def test_an_empty_global_sync_status_with_trial_balances_still_passes():
    """TB-only site: status never set (no connector, no Airbyte sync ever) —
    submitted trial balances alone are enough to build."""
    (ok, message), _ = check(rows=12, sync_status=None, sync_at=None)
    assert ok and "12 trial balance rows" in message, message


def test_skip_flag_bypasses_a_failed_global_sync():
    (ok, message), sqls = check(skip=1, sync_status="Failed", rows=12)
    # konsolidat#199: the skip path still asks the warehouse about claimed batches (rows, bases); no connector is consulted
    assert ok and "skip_airbyte_sync" in message and len(sqls) <= 2 and all("epm_raw" in s for s in sqls), message


# ---------------------------------------------------------------------------
# chart scope (konsol#182): @silver_main_accounts builds ERP staging/bronze
# models from epm_raw, so an enabled connector that never synced or is
# Failed/Running must still block it — same gate, same messages. But chart
# carries no trial-balance-rows requirement: a TB-only site must be able to
# build its chart before any TB exists, so no enabled connector means pass.
# ---------------------------------------------------------------------------
def check_chart(connectors=(), skip=0):
    """tasks._preflight_check("chart"), lifted with ast like ``check()``
    above. Stubs konsol.clickhouse.check_health (chart still needs a healthy
    warehouse) instead of konsol.clickhouse.execute, since a passing chart
    build must never query epm_raw.trial_balance_submissions."""
    settings = _Row(skip_airbyte_sync=skip)

    def get_all(doctype, filters=None, fields=None, limit_page_length=None):
        assert doctype == "Connector"
        return [_Row(c) for c in connectors if c.get("enabled", 1)]

    frappe = types.SimpleNamespace(get_single=lambda name: settings, get_all=get_all,
                                   db=types.SimpleNamespace(table_exists=lambda name: True))
    with open(TASKS) as f:
        tree = ast.parse(f.read())
    wanted = {"_preflight_check", "_check_chart_build_allowed", "_connector_sync_gate"}
    nodes = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in wanted]
    assert {n.name for n in nodes} == wanted
    ch = types.ModuleType("konsol.clickhouse")
    ch.check_health = lambda: {"status": "healthy"}
    saved = sys.modules.get("konsol.clickhouse")
    sys.modules["konsol.clickhouse"] = ch
    try:
        ns = {"frappe": frappe}
        exec(compile(ast.Module(body=nodes, type_ignores=[]), TASKS, "exec"), ns)
        return ns["_preflight_check"]("chart")
    finally:
        if saved is None:
            sys.modules.pop("konsol.clickhouse", None)
        else:
            sys.modules["konsol.clickhouse"] = saved


def test_chart_blocks_on_a_never_synced_connector():
    assert check_chart(connectors=NEVER_SYNCED) == (
        False, "Connector 'ZZ ERP' has never synced — epm_raw may be empty")


def test_chart_blocks_on_a_failed_connector():
    assert check_chart(connectors=FAILED) == (
        False, "Connector 'ZZ ERP' sync status is 'Failed' — cannot build from raw")


def test_chart_blocks_on_a_running_connector():
    assert check_chart(connectors=RUNNING) == (
        False, "Connector 'ZZ ERP' sync status is 'Running' — cannot build from raw")


def test_chart_passes_with_no_connector_and_zero_trial_balances():
    """A TB-only site must be able to build its chart before any TB exists."""
    ok, message = check_chart(connectors=())
    assert ok, message


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

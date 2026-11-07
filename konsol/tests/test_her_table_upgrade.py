"""epm_staging.historical_equity_rates gains to_currency in its sort key on an
existing stack (konsol#374, option b; Deepak Pai, 7 Nov 2026).

konsolidat's init-db.sql creates the table only on an empty ClickHouse volume,
so every running stack has the old shape: no to_currency column, ORDER BY
(consolidation_group, data_area_id, main_account, rate_date). CREATE TABLE IF
NOT EXISTS never touches it, and ADD COLUMN alone leaves the key without the
currency, so ReplacingMergeTree would collapse the EUR and the USD rate of one
tranche into one row on merge. ClickHouse changes a sorting key only by
appending, in the ALTER that adds the column, when the column has no DEFAULT
(measured on 24.8 for the sync watermark, konsolidat#246). So konsol owns the
table's DDL from here and upgrades an existing one with that one ALTER, asked
first from system.tables so an upgraded table is never altered again.

Exercises the real ensure_reference_tables() with frappe stubbed, like
test_reconcile_run_id.py.
"""
import importlib.util
import os
import sys
import types

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CH_PATH = os.path.join(APP_DIR, "clickhouse.py")

try:
    import requests  # noqa: F401 — clickhouse.py imports it at module level
except ModuleNotFoundError:  # host runner without requests — runner skips
    raise ImportError("needs requests")

TABLE = "epm_staging.historical_equity_rates"
#: konsolidat clickhouse/init-db.sql, verbatim (tests/test_historical_equity_rates_ddl.py
#: there pins the same string).
BODY = (
    "(consolidation_group String, data_area_id String, main_account String, "
    "rate_date Date, historical_rate Decimal(18,6), to_currency LowCardinality(String), "
    "updated_at DateTime DEFAULT now()) "
    "ENGINE = ReplacingMergeTree(updated_at) "
    "ORDER BY (consolidation_group, data_area_id, main_account, rate_date, to_currency)"
)
UPGRADE = (
    f"ALTER TABLE {TABLE} "
    "ADD COLUMN IF NOT EXISTS to_currency LowCardinality(String) AFTER historical_rate, "
    "MODIFY ORDER BY (consolidation_group, data_area_id, main_account, rate_date, to_currency)"
)


class _Logger:
    def __init__(self):
        self.messages = []

    def warning(self, msg, *a, **k):
        self.messages.append(str(msg))

    error = warning
    info = warning
    exception = warning


def _load():
    frappe = types.ModuleType("frappe")
    frappe.flags = types.SimpleNamespace(
        in_install=False, in_import=False, in_migrate=False, in_patch=False)
    frappe._logger = _Logger()
    frappe.logger = lambda: frappe._logger
    frappe.publish_realtime = lambda *a, **k: None
    frappe.get_meta = lambda dt: types.SimpleNamespace(is_submittable=0)
    frappe.get_all = lambda *a, **k: []
    frappe.db = types.SimpleNamespace(count=lambda *a, **k: 0)
    model = types.ModuleType("frappe.model")
    base_document = types.ModuleType("frappe.model.base_document")
    base_document.get_controller = lambda dt: None
    model.base_document = base_document
    frappe.model = model
    sys.modules.update({"frappe": frappe, "frappe.model": model,
                        "frappe.model.base_document": base_document})
    spec = importlib.util.spec_from_file_location("konsol_clickhouse_k374", CH_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module, frappe


def _probe(sorting_key):
    """ensure_reference_tables() with system.tables answering ``sorting_key``
    for the rates table ('' = the table does not exist yet). Returns every
    statement that names the table."""
    m, _ = _load()
    sql = []

    def execute(statement, params=None):
        sql.append(statement)
        if "sorting_key" in statement and "system.tables" in statement and "historical_equity_rates" in statement:
            return sorting_key
        return ""

    m.execute = execute
    m.ensure_reference_tables()
    return [s for s in sql if TABLE in s]


def test_konsol_creates_the_table_with_konsolidats_shape():
    m, _ = _load()
    assert m._REFERENCE_TABLE_DDL[TABLE] == BODY, m._REFERENCE_TABLE_DDL.get(TABLE)
    statements = _probe("")
    assert f"CREATE TABLE IF NOT EXISTS {TABLE} {BODY}" in statements, statements


def test_an_old_table_gains_the_column_and_the_key_in_one_alter():
    statements = _probe("consolidation_group, data_area_id, main_account, rate_date")
    alters = [s for s in statements if s.startswith(f"ALTER TABLE {TABLE}")]
    assert alters == [UPGRADE], alters
    assert "DEFAULT" not in UPGRADE
    create = next(i for i, s in enumerate(statements) if s.startswith("CREATE TABLE"))
    assert create < statements.index(UPGRADE), "CREATE IF NOT EXISTS first, then the ALTER"


def test_an_upgraded_table_is_not_altered_again():
    """An ALTER that changes nothing still rewrites metadata_modification_time
    (measured on 24.8): ask first, as the watermark upgrade does."""
    statements = _probe("consolidation_group, data_area_id, main_account, rate_date, to_currency")
    assert not [s for s in statements if s.startswith("ALTER TABLE")], statements


def test_a_missing_table_is_created_not_altered():
    statements = _probe("")
    assert not [s for s in statements if s.startswith("ALTER TABLE")], statements


def test_an_unreachable_server_defers_the_upgrade_to_the_next_migrate():
    m, frappe = _load()

    def execute(statement, params=None):
        if "system.tables" in statement:
            raise ConnectionError("down")
        return ""

    m.execute = execute
    assert m._sorting_key_upgrade_ddl(TABLE, "to_currency", UPGRADE) == []


def test_one_helper_serves_the_rates_table_and_the_watermark():
    """PR #436 review: _historical_rate_key_upgrade_ddl was a copy of
    _watermark_upgrade_ddl with the names swapped. One helper, two call sites."""
    m, _ = _load()
    assert not hasattr(m, "_historical_rate_key_upgrade_ddl")
    assert not hasattr(m, "_watermark_upgrade_ddl")
    src = open(CH_PATH).read()
    assert src.count("_sorting_key_upgrade_ddl(") == 3, "the definition and two call sites"
    assert '_sorting_key_upgrade_ddl(_WATERMARK_TABLE, "reconcile_run_id", _WATERMARK_UPGRADE)' in src
    assert '_sorting_key_upgrade_ddl(_HISTORICAL_RATE_TABLE, "to_currency", _HISTORICAL_RATE_UPGRADE)' in src


# --- a failed upgrade must not let a sync collapse the two currencies ----------

OLD_KEY = "consolidation_group, data_area_id, main_account, rate_date"
NEW_KEY = OLD_KEY + ", to_currency"
COLUMNS = ["consolidation_group", "data_area_id", "main_account", "rate_date",
           "historical_rate", "to_currency"]
ROWS = [["ZZGRP", "ZZNL", "ZZ3000", "2024-01-01", 1.1, "EUR"],
        ["ZZGRP", "ZZNL", "ZZ3000", "2024-01-01", 1.2, "USD"]]


def _stuck_stack(alter_error=RuntimeError("Cannot modify sorting key")):
    """A ClickHouse whose rates table keeps the OLD key: the upgrade ALTER
    fails (ensure_reference_tables logs it and goes on), and system.tables
    keeps answering the old key. Returns (module, frappe, statements)."""
    m, frappe = _load()
    sql = []

    def execute(statement, params=None):
        sql.append(statement)
        if statement == UPGRADE:
            raise alter_error
        if "sorting_key" in statement and "system.tables" in statement and "historical_equity_rates" in statement:
            return OLD_KEY
        return ""

    m.execute = execute
    return m, frappe, sql


def test_a_sync_into_the_old_key_is_refused_by_name_not_merged_silently():
    """PR #436 review: ensure_reference_tables swallows a failed ALTER (warning
    only), and reconcile_all then inserted to_currency rows into a table whose
    key lacks the column, so ReplacingMergeTree collapsed the EUR and the USD
    rate of one tranche. The sync now asks system.tables first and refuses."""
    m, frappe, sql = _stuck_stack()
    m.ensure_reference_tables()
    assert any("reference table bootstrap skipped" in msg for msg in frappe._logger.messages)
    before = len(sql)
    try:
        m.sync_table(TABLE, COLUMNS, ROWS, force=True)
    except m.SortingKeyStale as e:
        assert e.table == TABLE and e.column == "to_currency" and e.key == OLD_KEY
        assert TABLE in str(e) and "to_currency" in str(e)
    else:
        raise AssertionError("a sync into the old sorting key must be refused")
    written = [s for s in sql[before:] if s.startswith(("INSERT", "EXCHANGE", "CREATE TABLE", "DROP"))]
    assert written == [], written
    assert m._sync_failures[TABLE]["error_type"] == "sorting_key_stale"
    assert any("SYNC REFUSED" in msg and TABLE in msg for msg in frappe._logger.messages)


def test_an_unforced_sync_is_refused_too():
    """A hook's after-commit sync would collapse the rows just the same: it is
    not downtime, so it does not degrade to None like a connection error."""
    m, _, _ = _stuck_stack()
    try:
        m.sync_table(TABLE, COLUMNS, ROWS)
    except m.SortingKeyStale:
        pass
    else:
        raise AssertionError("an unforced sync into the old key must be refused")


def test_an_upgraded_table_syncs_and_a_missing_one_is_left_to_the_insert():
    for key in (NEW_KEY, ""):
        m, _ = _load()
        sql = []

        def execute(statement, params=None, key=key):
            sql.append(statement)
            if "sorting_key" in statement and "system.tables" in statement:
                return key
            return ""

        m.execute = execute
        assert m.sync_table(TABLE, COLUMNS, ROWS, force=True) == 2
        assert any(s.startswith(f"INSERT INTO {TABLE}_sync_tmp") for s in sql), (key, sql)


def test_an_unreachable_server_fails_the_sync_on_its_own_terms():
    """system.tables cannot be asked: the refusal does not fire (there is
    nothing to judge), and the sync fails through its existing branches."""
    import requests
    m, _ = _load()

    def execute(statement, params=None):
        raise requests.exceptions.ConnectionError("down")

    m.execute = execute
    try:
        m.sync_table(TABLE, COLUMNS, ROWS, force=True)
    except requests.exceptions.ConnectionError:
        pass
    else:
        raise AssertionError("expected the connection error")


def test_reconcile_all_names_the_refused_table_in_its_result():
    """reconcile_all's dict is what the reconcile job and the migrate log read:
    table -> None is 'NOT synced', by name, like a table ClickHouse refused."""

    class _Rates:
        CH_TABLE = TABLE
        CH_FIELD_MAP = dict(zip(COLUMNS, COLUMNS))

    class _Other:
        CH_TABLE = "epm_staging.zz_other"
        CH_FIELD_MAP = {"code": "code"}

    m, frappe, sql = _stuck_stack()
    controllers = {"Historical Equity Rate": _Rates, "ZZ Other": _Other}
    sys.modules["frappe.model.base_document"].get_controller = lambda dt: controllers[dt]

    def get_all(doctype, filters=None, fields=None, pluck=None, limit_page_length=None, **k):
        if doctype == "Module Def":
            return ["ZZ"]
        if doctype == "DocType":
            return list(controllers)
        if doctype == "Historical Equity Rate":
            return [dict(zip(COLUMNS, r), modified=None) for r in ROWS]
        return [{"code": "ZZ1", "modified": None}]

    frappe.get_all = get_all
    synced = m.reconcile_all()
    assert synced == {TABLE: None, "epm_staging.zz_other": 1}, synced
    assert any("NOT synced" in msg and TABLE in msg for msg in frappe._logger.messages)
    assert not any(s.startswith(f"INSERT INTO {TABLE}") for s in sql)

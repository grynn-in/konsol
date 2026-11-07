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
    assert m._historical_rate_key_upgrade_ddl() == []

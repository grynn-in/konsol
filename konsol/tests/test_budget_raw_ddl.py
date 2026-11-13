"""Budget S2 (I3): the raw budget landing table, epm_raw.budget_entries.

Two repos create it: konsolidat's clickhouse/init-db.sql on a fresh volume,
konsol's _REFERENCE_TABLE_DDL on every migrate. The string below is the
contract, verbatim; konsolidat's tests/test_budget_raw_ddl.py (S2-R1) pins the
same text.

One row per Budget Entry Line: its entry, the header the entry stamps on it
(cycle, scenario, layer, entity, year, period, currency, amount basis), the
account, and debit and credit (Q3-a, Decimal(18,4)). No partner column: a
partner header is refused at intake, naming #427 (Q2-c). amount_basis has no
DEFAULT: it is required and never defaulted (Q2-c). The site's dim_* columns
are not in the CREATE; the declared in_budget dimensions are added by ALTER
(S2-I4), as on the other budget tables (konsol#287).

The column names are Budget Entry Line's CH_FIELD_MAP keys, in order, so the
write-through sends exactly the columns the table has.
"""
import ast
import importlib.util
import os
import sys
import types

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CH_PATH = os.path.join(APP_DIR, "clickhouse.py")
BEL_PATH = os.path.join(APP_DIR, "epm", "doctype", "budget_entry_line",
                        "budget_entry_line.py")

try:
    import requests  # noqa: F401 — clickhouse.py imports it at module level
except ModuleNotFoundError:  # host runner without requests — runner skips
    raise ImportError("needs requests")

TABLE = "epm_raw.budget_entries"

#: konsolidat clickhouse/init-db.sql, verbatim (S2-R1 pins the same string).
BODY = (
    "(entry_name String, cycle String, scenario_id String, layer String, "
    "data_area_id String, fiscal_year UInt16, fiscal_period UInt8, "
    "main_account String, debit Decimal(18,4), credit Decimal(18,4), "
    "currency String, amount_basis String) "
    "ENGINE = MergeTree "
    "ORDER BY (scenario_id, data_area_id, fiscal_year, fiscal_period, layer, main_account)"
)


def _literal(path, name):
    with open(path) as f:
        tree = ast.parse(f.read())
    for node in tree.body:
        if (isinstance(node, ast.Assign) and len(node.targets) == 1
                and getattr(node.targets[0], "id", None) == name):
            return ast.literal_eval(node.value)
    raise AssertionError(f"{name} not found in {path}")


def _columns(body):
    return [part.split()[0] for part in body[1:body.index(") ENGINE")].split(", ")]


class _Logger:
    def __init__(self):
        self.messages = []

    def warning(self, msg, *a, **k):
        self.messages.append(str(msg))

    error = info = exception = warning


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
    spec = importlib.util.spec_from_file_location("konsol_clickhouse_s2i3", CH_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_konsol_creates_the_table_with_konsolidats_shape():
    assert _literal(CH_PATH, "_REFERENCE_TABLE_DDL").get(TABLE) == BODY


def test_the_columns_are_the_line_field_map():
    """The write-through sends CH_FIELD_MAP's keys as the column list; a key
    the table lacks fails the sync, a column the map lacks is never filled."""
    assert _columns(BODY) == list(_literal(BEL_PATH, "FIXED_FIELD_MAP"))


def test_no_partner_column_no_dimension_and_no_defaulted_basis():
    columns = _columns(BODY)
    assert "partner_data_area_id" not in columns
    assert not [c for c in columns if c.startswith("dim_")]
    assert "DEFAULT" not in BODY


def test_a_migrate_creates_the_database_before_the_table():
    """ensure_reference_tables() runs the reference CREATEs before
    ensure_raw_tables() creates epm_raw, so on a stack without the database
    the CREATE would fail and be skipped. The database comes first."""
    m = _load()
    sql = []
    m.execute = lambda s, params=None: sql.append(s) or ""
    m.ensure_reference_tables()
    create = sql.index(f"CREATE TABLE IF NOT EXISTS {TABLE} {BODY}")
    assert sql.index("CREATE DATABASE IF NOT EXISTS epm_raw") < create

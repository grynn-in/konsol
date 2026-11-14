"""Budget S2 (A7, Q13-a): epm_gold.budget_annual_input carries layer and currency.

Two repos create the table: konsolidat's clickhouse/init-db.sql on a fresh
volume, konsol's _REFERENCE_TABLE_DDL on every migrate. The string below is
the contract, verbatim; konsolidat's tests/test_budget_annual_input_ddl.py
(S2-R6) pins the same text.

Budget Annual Input now has a required layer and a required currency (A5), so
the warehouse row carries both. Neither has a DEFAULT: both are required on the
document, so a row never arrives without them. They sit after main_account, as
on the doctype. An existing table gets them by ALTER ... AFTER, through
_POSITIONED_COLUMNS, so an upgraded table has the fresh table's column order
even when the site's dim_* columns were already added after submitted_by.
The site's dim_* columns are not in the CREATE (konsol#287).
"""
import ast
import importlib.util
import os
import sys
import types

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CH_PATH = os.path.join(APP_DIR, "clickhouse.py")
BAI_PATH = os.path.join(APP_DIR, "epm", "doctype", "budget_annual_input",
                        "budget_annual_input.py")

try:
    import requests  # noqa: F401 — clickhouse.py imports it at module level
except ModuleNotFoundError:  # host runner without requests — runner skips
    raise ImportError("needs requests")

TABLE = "epm_gold.budget_annual_input"

#: konsolidat clickhouse/init-db.sql, verbatim (S2-R6 pins the same string).
BODY = (
    "(scenario_id String, data_area_id String, fiscal_year UInt16, "
    "main_account String, layer String, currency String, "
    "annual_amount Decimal(18,2), spread_profile_id String, "
    "submitted_by String) "
    "ENGINE = MergeTree ORDER BY (scenario_id, data_area_id, fiscal_year, main_account)"
)

OLD_COLUMNS = ["scenario_id", "data_area_id", "fiscal_year", "main_account",
               "annual_amount", "spread_profile_id", "submitted_by"]

UPGRADE = (
    f"ALTER TABLE {TABLE} "
    "ADD COLUMN IF NOT EXISTS layer String AFTER main_account, "
    "ADD COLUMN IF NOT EXISTS currency String AFTER layer"
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
    spec = importlib.util.spec_from_file_location("konsol_clickhouse_s2a7", CH_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _probe(columns):
    """ensure_reference_tables() with system.columns answering ``columns``
    ({table: [column names]}; a table absent from it does not exist yet).
    Returns every statement issued."""
    m = _load()
    sql = []

    def execute(statement, params=None):
        sql.append(statement)
        if "system.columns" in statement:
            rows = []
            for table, names in columns.items():
                database, name = table.split(".", 1)
                rows += [f"{database}\t{name}\t{c}" for c in names]
            return "\n".join(rows)
        return ""

    m.execute = execute
    m.ensure_reference_tables()
    return sql


def _alters(sql):
    return [s for s in sql if s.startswith(f"ALTER TABLE {TABLE} ADD COLUMN")]


def test_konsol_creates_the_table_with_konsolidats_shape():
    assert _literal(CH_PATH, "_REFERENCE_TABLE_DDL").get(TABLE) == BODY


def test_the_columns_are_the_annual_input_field_map():
    """The write-through sends CH_FIELD_MAP's keys as the column list; a key
    the table lacks fails the sync, a column the map lacks is never filled."""
    assert _columns(BODY) == list(_literal(BAI_PATH, "FIXED_FIELD_MAP"))


def test_layer_and_currency_have_no_default_and_no_dimension_is_named():
    assert "layer String, currency String," in BODY
    assert "DEFAULT" not in BODY
    assert not [c for c in _columns(BODY) if c.startswith("dim_")]


def test_an_existing_table_gets_them_where_the_create_puts_them():
    positioned = _literal(CH_PATH, "_POSITIONED_COLUMNS")
    assert positioned.get(TABLE) == [("layer", "String", "main_account"),
                                     ("currency", "String", "layer")]
    names = _columns(BODY)
    for column, typ, after in positioned[TABLE]:
        assert names.index(column) == names.index(after) + 1, (column, after)
        assert f"{column} {typ}" in BODY, column


def test_an_old_table_gains_both_in_one_alter_after_its_create():
    sql = _probe({TABLE: OLD_COLUMNS + ["dim_region"]})
    assert _alters(sql) == [UPGRADE], _alters(sql)
    create = sql.index(f"CREATE TABLE IF NOT EXISTS {TABLE} {BODY}")
    assert create < sql.index(UPGRADE), "CREATE IF NOT EXISTS first, then the ALTER"


def test_an_upgraded_table_is_not_altered_again():
    assert _alters(_probe({TABLE: _columns(BODY) + ["dim_region"]})) == []


def test_a_missing_table_is_created_not_altered():
    sql = _probe({})
    assert _alters(sql) == []
    assert f"CREATE TABLE IF NOT EXISTS {TABLE} {BODY}" in sql

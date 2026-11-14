"""konsol#117 pair 1, task C: the ownership graph's warehouse shape.

Three epm_staging tables carry the graph, and two repos create them:
konsolidat's clickhouse/init-db.sql on a fresh volume, konsol's
_REFERENCE_TABLE_DDL on every migrate. The strings below are the contract,
verbatim; konsolidat's tests/test_ownership_graph_ddl.py pins the same text.

* ownership_periods gains the LINK'S OWNER (owner_group, owner_data_area_id),
  LAST in the column list and appended LAST to the sorting key: two links into
  one node from one date share the old key (consolidation_group, data_area_id,
  effective_date) and ReplacingMergeTree would collapse them on merge (task E
  finding, 7 Nov 2026). No DEFAULT, because ClickHouse appends to a sorting key
  only a column the same ALTER adds without one (konsol#374); the upgrade is
  ONE statement through _sorting_key_upgrade_ddl, and
  _REQUIRED_SORTING_KEY_COLUMNS makes a sync into the old key refuse by name.
* consolidation_ancestry gains path_id (the full link path, unique per path
  from the ancestor to the entity), link_owner_group and
  link_owner_data_area_id after path: one entity reached by two paths has two
  path_ids, and dbt sums the per-path products instead of multiplying every
  link it finds.
* consolidation_method_declarations is new: the method a group declares for
  an entity its links reach by more than one path (design comment section 1).

CREATE TABLE IF NOT EXISTS never touches an existing table, so an existing
stack gets the columns by ALTER TABLE ... ADD COLUMN IF NOT EXISTS, placed
AFTER the same column the CREATE puts them after (a fresh and an upgraded
table read the same), asked first (system.columns for the ancestry,
system.tables' sorting_key for the periods) so an upgraded table is never
altered again: an ALTER that changes nothing still rewrites
metadata_modification_time. consolidation_ancestry keeps its ORDER BY.
"""
import ast
import importlib.util
import os
import sys
import types

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CH_PATH = os.path.join(APP_DIR, "clickhouse.py")
CG_PATH = os.path.join(APP_DIR, "consolidation", "doctype", "consolidation_group",
                       "consolidation_group.py")
OP_PATH = os.path.join(APP_DIR, "consolidation", "doctype", "ownership_period",
                       "ownership_period.py")

try:
    import requests  # noqa: F401 — clickhouse.py imports it at module level
except ModuleNotFoundError:  # host runner without requests — runner skips
    raise ImportError("needs requests")

PERIODS = "epm_staging.ownership_periods"
ANCESTRY = "epm_staging.consolidation_ancestry"
DECLARATIONS = "epm_staging.consolidation_method_declarations"

#: konsolidat clickhouse/init-db.sql, verbatim.
BODIES = {
    PERIODS: (
        "(consolidation_group String, data_area_id String, effective_date Date, "
        "end_date Date DEFAULT '9999-12-31', ownership_pct Decimal(5,2), "
        "consolidation_method String DEFAULT 'full', acquisition_date Date DEFAULT '1900-01-01', "
        "is_first_acquisition UInt8 DEFAULT 0, acquisition_price Decimal(18,2) DEFAULT 0, "
        "fair_value_adjustment Decimal(18,2) DEFAULT 0, disposal_date Date DEFAULT '9999-12-31', "
        "disposal_price Decimal(18,2) DEFAULT 0, is_disposal UInt8 DEFAULT 0, "
        "updated_at DateTime DEFAULT now(), owner_group String, owner_data_area_id String) "
        "ENGINE = ReplacingMergeTree(updated_at) "
        "ORDER BY (consolidation_group, data_area_id, effective_date, owner_group, owner_data_area_id)"
    ),
    ANCESTRY: (
        "(consolidation_group String, data_area_id String, link_group String, "
        "link_data_area_id String, link_depth UInt8, depth UInt8, path String, "
        "path_id String, link_owner_group String, link_owner_data_area_id String) "
        "ENGINE = MergeTree ORDER BY (consolidation_group, data_area_id, link_depth)"
    ),
    DECLARATIONS: (
        "(consolidation_group String, data_area_id String, consolidation_method String, "
        "effective_date Date, end_date Date DEFAULT '9999-12-31', reason String, "
        "updated_at DateTime DEFAULT now()) "
        "ENGINE = ReplacingMergeTree(updated_at) "
        "ORDER BY (consolidation_group, data_area_id, effective_date)"
    ),
}
#: The upgrade ALTER for a table created before the columns, verbatim.
UPGRADES = {
    PERIODS: (
        f"ALTER TABLE {PERIODS} "
        "ADD COLUMN IF NOT EXISTS owner_group String AFTER updated_at, "
        "ADD COLUMN IF NOT EXISTS owner_data_area_id String AFTER owner_group, "
        "MODIFY ORDER BY (consolidation_group, data_area_id, effective_date, owner_group, owner_data_area_id)"
    ),
    ANCESTRY: (
        f"ALTER TABLE {ANCESTRY} "
        "ADD COLUMN IF NOT EXISTS path_id String AFTER path, "
        "ADD COLUMN IF NOT EXISTS link_owner_group String AFTER path_id, "
        "ADD COLUMN IF NOT EXISTS link_owner_data_area_id String AFTER link_owner_group"
    ),
}
OLD_COLUMNS = {
    ANCESTRY: ["consolidation_group", "data_area_id", "link_group", "link_data_area_id",
               "link_depth", "depth", "path"],
}
OLD_KEY = "consolidation_group, data_area_id, effective_date"
NEW_KEY = OLD_KEY + ", owner_group, owner_data_area_id"


def _columns(body):
    return [part.split()[0] for part in body[1:body.index(") ENGINE")].split(",")]


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
    spec = importlib.util.spec_from_file_location("konsol_clickhouse_k117c", CH_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module, frappe


def _probe(columns, periods_key=""):
    """ensure_reference_tables() with system.columns answering ``columns``
    ({table: [column names]}; a table absent from it does not exist yet) and
    system.tables answering ``periods_key`` as ownership_periods' sorting key
    ('' = the table does not exist yet). Returns every statement issued."""
    m, _ = _load()
    sql = []

    def execute(statement, params=None):
        sql.append(statement)
        if "system.columns" in statement:
            rows = []
            for table, names in columns.items():
                database, name = table.split(".", 1)
                rows += [f"{database}\t{name}\t{c}" for c in names]
            return "\n".join(rows)
        if "sorting_key" in statement and "system.tables" in statement and "ownership_periods" in statement:
            return periods_key
        return ""

    m.execute = execute
    m.ensure_reference_tables()
    return sql


def _literal(name):
    with open(CH_PATH) as f:
        tree = ast.parse(f.read())
    for node in tree.body:
        if (isinstance(node, ast.Assign) and len(node.targets) == 1
                and getattr(node.targets[0], "id", None) == name):
            return ast.literal_eval(node.value)
    raise AssertionError(f"{name} not found in clickhouse.py")


# --- the three tables, verbatim ------------------------------------------------

def test_konsol_creates_the_three_tables_with_konsolidats_shape():
    ddl = _literal("_REFERENCE_TABLE_DDL")
    for table, body in BODIES.items():
        assert ddl.get(table) == body, (table, ddl.get(table))
    statements = _probe({})
    for table, body in BODIES.items():
        assert f"CREATE TABLE IF NOT EXISTS {table} {body}" in statements, table


def test_the_ancestry_key_is_unchanged_and_the_owner_ends_the_periods_key():
    """A sorting key can only be appended to, by the ALTER that adds the column,
    and only without a DEFAULT; the fresh shape must match the upgraded one."""
    assert BODIES[ANCESTRY].endswith("ORDER BY (consolidation_group, data_area_id, link_depth)")
    assert BODIES[PERIODS].endswith(f"ORDER BY ({NEW_KEY})")
    tail = BODIES[PERIODS][:BODIES[PERIODS].index(") ENGINE")]
    assert tail.endswith("updated_at DateTime DEFAULT now(), owner_group String, owner_data_area_id String")
    assert "MODIFY ORDER BY (" + NEW_KEY + ")" in UPGRADES[PERIODS]
    assert "DEFAULT" not in UPGRADES[PERIODS]


def test_the_new_columns_sit_where_the_upgrade_puts_them():
    """_POSITIONED_COLUMNS: (column, type, after). Each column must follow its
    anchor in the CREATE, so a fresh table and an upgraded one agree."""
    positioned = _literal("_POSITIONED_COLUMNS")
    assert positioned[ANCESTRY] == [("path_id", "String", "path"),
                                    ("link_owner_group", "String", "path_id"),
                                    ("link_owner_data_area_id", "String", "link_owner_group")]
    assert PERIODS not in positioned, \
        "the periods table's owner is a sorting-key upgrade, not a positioned column"
    # Other tables' positioned columns are pinned beside their own DDL
    # (budget_annual_input: test_budget_annual_input_ddl.py).
    for table, cols in ((t, c) for t, c in positioned.items() if t in BODIES):
        names = _columns(BODIES[table])
        for column, _typ, after in cols:
            assert names.index(column) == names.index(after) + 1, (table, column, after)
        for column, typ, _after in cols:
            assert f"{column} {typ}" in BODIES[table], (table, column)


# --- the upgrade of an existing stack -----------------------------------------

def test_an_old_table_gains_its_columns_in_one_alter_after_its_create():
    statements = _probe(OLD_COLUMNS, periods_key=OLD_KEY)
    for table, upgrade in UPGRADES.items():
        alters = [s for s in statements if s.startswith(f"ALTER TABLE {table} ADD COLUMN")]
        assert alters == [upgrade], (table, alters)
        create = statements.index(f"CREATE TABLE IF NOT EXISTS {table} {BODIES[table]}")
        assert create < statements.index(upgrade), "CREATE IF NOT EXISTS first, then the ALTER"


def test_an_upgraded_table_is_not_altered_again():
    """Asked first, so a migrate on an upgraded stack issues nothing."""
    statements = _probe({t: _columns(b) for t, b in BODIES.items()}, periods_key=NEW_KEY)
    alters = [s for s in statements if s.startswith("ALTER TABLE") and "ADD COLUMN" in s
              and (PERIODS in s or ANCESTRY in s)]
    assert alters == [], alters


def test_the_periods_upgrade_goes_through_the_374_helper():
    """One helper for the watermark, the rates table and now the periods: the
    key is read from system.tables and the ALTER issued only while owner_group
    is not in it (test_her_table_upgrade.py counts the call sites)."""
    m, _ = _load()
    src = open(CH_PATH).read()
    assert '_sorting_key_upgrade_ddl(_OWNERSHIP_PERIODS_TABLE, "owner_group", _OWNERSHIP_PERIODS_UPGRADE)' in src
    assert m._OWNERSHIP_PERIODS_UPGRADE == UPGRADES[PERIODS]
    assert m._REQUIRED_SORTING_KEY_COLUMNS[PERIODS] == "owner_group"


def test_a_sync_into_the_old_periods_key_is_refused_by_name():
    """ensure_reference_tables swallows a failed ALTER; without this, a sync
    would let ReplacingMergeTree collapse two owners' links into one row."""
    m, frappe = _load()
    sql = []

    def execute(statement, params=None):
        sql.append(statement)
        if "sorting_key" in statement and "system.tables" in statement and "ownership_periods" in statement:
            return OLD_KEY
        return ""

    m.execute = execute
    columns = ["consolidation_group", "data_area_id", "effective_date", "ownership_pct",
               "owner_group", "owner_data_area_id"]
    rows = [["ZZG", "ZZD", "2026-01-01", 60, "ZZG", "ZZA"], ["ZZG", "ZZD", "2026-01-01", 20, "ZZG", ""]]
    try:
        m.sync_table(PERIODS, columns, rows, force=True)
    except m.SortingKeyStale as e:
        assert (e.table, e.column, e.key) == (PERIODS, "owner_group", OLD_KEY)
    else:
        raise AssertionError("a sync into the old sorting key must be refused")
    assert not [s for s in sql if s.startswith(("INSERT", "EXCHANGE"))], sql


def test_a_partly_upgraded_table_gets_only_what_it_lacks():
    """A stack where one ALTER landed and the next did not (a reconnect in
    between) is finished, not refused: each missing column, in CREATE order."""
    statements = _probe({ANCESTRY: OLD_COLUMNS[ANCESTRY] + ["path_id"]})
    alters = [s for s in statements if s.startswith(f"ALTER TABLE {ANCESTRY} ADD COLUMN")]
    assert alters == [
        f"ALTER TABLE {ANCESTRY} "
        "ADD COLUMN IF NOT EXISTS link_owner_group String AFTER path_id, "
        "ADD COLUMN IF NOT EXISTS link_owner_data_area_id String AFTER link_owner_group"
    ], alters


def test_a_missing_table_is_created_not_altered():
    statements = _probe({}, periods_key="")
    alters = [s for s in statements if s.startswith("ALTER TABLE") and "ADD COLUMN" in s
              and (PERIODS in s or ANCESTRY in s)]
    assert alters == [], alters


def test_an_unreachable_server_defers_the_upgrade_to_the_next_migrate():
    m, _ = _load()

    def execute(statement, params=None):
        if "system.columns" in statement:
            raise ConnectionError("down")
        return ""

    m.execute = execute
    assert m._positioned_column_ddl() == []


def test_the_probe_asks_system_columns_once():
    statements = _probe(OLD_COLUMNS, periods_key=OLD_KEY)
    probes = [s for s in statements if "system.columns" in s and "consolidation_ancestry" in s]
    assert len(probes) == 1, probes


# --- the writers agree with the shape ------------------------------------------

def _class_literal(path, class_name, attr):
    with open(path) as f:
        tree = ast.parse(f.read())
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            for stmt in node.body:
                if (isinstance(stmt, ast.Assign) and len(stmt.targets) == 1
                        and getattr(stmt.targets[0], "id", None) == attr):
                    return ast.literal_eval(stmt.value)
    raise AssertionError(f"{class_name}.{attr} not found in {path}")


def test_the_closure_writes_every_ancestry_column():
    columns = _class_literal(CG_PATH, "ConsolidationGroup", "CH_STAGING_COLUMNS")
    assert columns == _columns(BODIES[ANCESTRY]), columns


def test_the_period_sync_names_only_warehouse_columns():
    """Task A adds the owner columns to CH_FIELD_MAP; whatever it names must be
    a column of the table, and the two owner columns default to '' until then."""
    field_map = _class_literal(OP_PATH, "OwnershipPeriod", "CH_FIELD_MAP")
    columns = _columns(BODIES[PERIODS])
    missing = [k for k in field_map if k not in columns]
    assert missing == [], missing
    assert "owner_group" in columns and "owner_data_area_id" in columns
